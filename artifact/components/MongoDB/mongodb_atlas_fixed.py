"""
MongoDB Atlas Vector Store — Custom Component (Bug Fixes)
==========================================================

WHY THIS COMPONENT EXISTS
--------------------------
This is a drop-in replacement for Langflow's built-in "MongoDB Atlas" component.
It fixes two confirmed bugs in the lfx package (Langflow <=1.8.2).

BUG 1 — BSON encoding of Pydantic objects in metadata
------------------------------------------------------
Langflow passes data between components as `Data` objects. These objects carry an
internal `Properties` Pydantic model in their metadata dictionary — used by Langflow
for display purposes (e.g. text_color, source, icon, usage stats). Example:

    Properties(
        text_color=None, background_color=None, edited=False,
        source=Source(id=None, display_name=None, source=None),
        ...
    )

When the built-in MongoDB Atlas component receives `Data` objects and calls
`to_lc_document()`, it copies `.data` directly into the LangChain `Document.metadata`
dict — including those `Properties` Pydantic model instances.

MongoDB's BSON encoder only understands Python primitives (str, int, dict, list, etc.).
It does NOT know how to serialize a Pydantic model object, so it raises:

    bson.errors.InvalidDocument: cannot encode object:
    Properties(...), of type: <class 'lfx.schema.properties.Properties'>

FIX: Before inserting, recursively walk the metadata and call .model_dump() on any
Pydantic BaseModel instances, converting them to plain dicts.

BUG 2 — Invalid quantization value "null" sent to MongoDB
----------------------------------------------------------
The built-in component's Quantization dropdown has options ["scalar", "binary"] with
a default value of None (Python null). When the user leaves it blank, the component
passes null to MongoDB's index definition.

MongoDB Atlas rejects null and requires one of: "none", "scalar", "binary". Error:

    Invalid quantization cannot be null.
    Allowed values are: none, scalar, binary.

FIX: Add "none" as the first/default option in the Quantization dropdown. When
creating the vector search index, omit the quantization field entirely if the value
is None or "none" (MongoDB's default behavior when the field is absent).

UPGRADE SAFETY
--------------
Because this lives in artifact/components/ (not inside .venv), it survives
`pip install --upgrade langflow`. No need to re-patch after upgrades.

USAGE
-----
In your Langflow flow, replace the built-in "MongoDB Atlas" component with
"MongoDB Atlas (Fixed)". All inputs and outputs are identical.
"""

import tempfile
import time

import certifi
from langchain_community.vectorstores import MongoDBAtlasVectorSearch
from langchain_core.documents import Document
from pydantic import BaseModel
from pymongo.collection import Collection
from pymongo.operations import SearchIndexModel

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.helpers.data import docs_to_data
from lfx.io import BoolInput, DropdownInput, HandleInput, IntInput, SecretStrInput, StrInput
from lfx.schema.data import Data


def _sanitize_metadata(metadata: dict) -> dict:
    """Recursively convert any Pydantic BaseModel instances to plain dicts.

    MongoDB's BSON encoder cannot serialize Pydantic models. This function
    walks the metadata dict and calls .model_dump() on any Pydantic objects,
    making the entire structure safe for BSON encoding.
    """
    result = {}
    for key, value in metadata.items():
        if isinstance(value, BaseModel):
            result[key] = _sanitize_metadata(value.model_dump())
        elif isinstance(value, dict):
            result[key] = _sanitize_metadata(value)
        elif isinstance(value, list):
            result[key] = [
                _sanitize_metadata(v.model_dump()) if isinstance(v, BaseModel)
                else _sanitize_metadata(v) if isinstance(v, dict)
                else v
                for v in value
            ]
        else:
            result[key] = value
    return result


class MongoDBAtlasFixed(LCVectorStoreComponent):
    """MongoDB Atlas Vector Store with metadata sanitization and quantization fixes.

    Drop-in replacement for the built-in MongoDB Atlas component.
    See module docstring for full explanation of the two bugs fixed here.
    """

    display_name = "MongoDB Atlas (Fixed)"
    description = "MongoDB Atlas Vector Store with search capabilities"
    name = "MongoDBAtlasVectorFixed"
    icon = "MongoDB"

    INSERT_MODES = ["append", "overwrite"]
    SIMILARITY_OPTIONS = ["cosine", "euclidean", "dotProduct"]
    # Bug 2 fix: "none" added as the default option (built-in only had scalar/binary)
    QUANTIZATION_OPTIONS = ["none", "scalar", "binary"]

    inputs = [
        SecretStrInput(name="mongodb_atlas_cluster_uri", display_name="MongoDB Atlas Cluster URI", required=True),
        BoolInput(name="enable_mtls", display_name="Enable mTLS", value=False, advanced=True, required=True),
        SecretStrInput(
            name="mongodb_atlas_client_cert",
            display_name="MongoDB Atlas Combined Client Certificate",
            required=False,
            info="Client Certificate combined with the private key in the following format:\n "
            "-----BEGIN PRIVATE KEY-----\n...\n -----END PRIVATE KEY-----\n-----BEGIN CERTIFICATE-----\n"
            "...\n-----END CERTIFICATE-----\n",
        ),
        StrInput(name="db_name", display_name="Database Name", required=True),
        StrInput(name="collection_name", display_name="Collection Name", required=True),
        StrInput(
            name="index_name",
            display_name="Index Name",
            required=True,
            info="The name of Atlas Search index, it should be a Vector Search.",
        ),
        *LCVectorStoreComponent.inputs,
        DropdownInput(
            name="insert_mode",
            display_name="Insert Mode",
            options=INSERT_MODES,
            value=INSERT_MODES[0],
            info="How to insert new documents into the collection.",
            advanced=True,
        ),
        HandleInput(name="embedding", display_name="Embedding", input_types=["Embeddings"]),
        IntInput(
            name="number_of_results",
            display_name="Number of Results",
            info="Number of results to return.",
            value=4,
            advanced=True,
        ),
        StrInput(
            name="index_field",
            display_name="Index Field",
            advanced=True,
            required=True,
            info="The field to index.",
            value="embedding",
        ),
        StrInput(
            name="filter_field", display_name="Filter Field", advanced=True, info="The field to filter the index."
        ),
        IntInput(
            name="number_dimensions",
            display_name="Number of Dimensions",
            info="Embedding Context Length.",
            value=1536,
            advanced=True,
            required=True,
        ),
        DropdownInput(
            name="similarity",
            display_name="Similarity",
            options=SIMILARITY_OPTIONS,
            value=SIMILARITY_OPTIONS[0],
            info="The method used to measure the similarity between vectors.",
            advanced=True,
        ),
        DropdownInput(
            name="quantization",
            display_name="Quantization",
            options=QUANTIZATION_OPTIONS,
            value=QUANTIZATION_OPTIONS[0],  # defaults to "none"
            info="Quantization reduces memory costs converting 32-bit floats to smaller data types",
            advanced=True,
        ),
    ]

    @check_cached_vector_store
    def build_vector_store(self) -> MongoDBAtlasVectorSearch:
        try:
            from pymongo import MongoClient
        except ImportError as e:
            msg = "Please install pymongo to use MongoDB Atlas Vector Store"
            raise ImportError(msg) from e

        client_cert_path = None
        if self.enable_mtls:
            try:
                client_cert = self.mongodb_atlas_client_cert.replace(" ", "\n")
                client_cert = client_cert.replace("-----BEGIN\nPRIVATE\nKEY-----", "-----BEGIN PRIVATE KEY-----")
                client_cert = client_cert.replace(
                    "-----END\nPRIVATE\nKEY-----\n-----BEGIN\nCERTIFICATE-----",
                    "-----END PRIVATE KEY-----\n-----BEGIN CERTIFICATE-----",
                )
                client_cert = client_cert.replace("-----END\nCERTIFICATE-----", "-----END CERTIFICATE-----")
                with tempfile.NamedTemporaryFile(delete=False) as f:
                    f.write(client_cert.encode("utf-8"))
                    client_cert_path = f.name
            except Exception as e:
                msg = f"Failed to write certificate to temporary file: {e}"
                raise ValueError(msg) from e

        try:
            mongo_client: MongoClient = (
                MongoClient(
                    self.mongodb_atlas_cluster_uri,
                    tls=True,
                    tlsCertificateKeyFile=client_cert_path,
                    tlsCAFile=certifi.where(),
                )
                if self.enable_mtls
                else MongoClient(self.mongodb_atlas_cluster_uri)
            )
            collection = mongo_client[self.db_name][self.collection_name]
        except Exception as e:
            msg = f"Failed to connect to MongoDB Atlas: {e}"
            raise ValueError(msg) from e

        self.ingest_data = self._prepare_ingest_data()

        documents = []
        for item in self.ingest_data or []:
            # Convert Data to LangChain Document
            doc = item.to_lc_document() if isinstance(item, Data) else item
            # Bug 1 fix: sanitize metadata so Pydantic models don't break BSON encoding
            doc = Document(
                page_content=doc.page_content,
                metadata=_sanitize_metadata(doc.metadata),
            )
            documents.append(doc)

        if documents:
            self.__insert_mode(collection)
            return MongoDBAtlasVectorSearch.from_documents(
                documents=documents,
                embedding=self.embedding,
                collection=collection,
                index_name=self.index_name,
            )

        return MongoDBAtlasVectorSearch(
            embedding=self.embedding,
            collection=collection,
            index_name=self.index_name,
        )

    def search_documents(self) -> list[Data]:
        from bson.objectid import ObjectId

        vector_store = self.build_vector_store()
        self.verify_search_index(vector_store._collection)

        if self.search_query and isinstance(self.search_query, str):
            docs = vector_store.similarity_search(
                query=self.search_query,
                k=self.number_of_results,
            )
            for doc in docs:
                doc.metadata = {
                    key: str(value) if isinstance(value, ObjectId) else value
                    for key, value in doc.metadata.items()
                }
            data = docs_to_data(docs)
            self.status = data
            return data
        return []

    def __insert_mode(self, collection: Collection) -> None:
        if self.insert_mode == "overwrite":
            collection.delete_many({})

    def verify_search_index(self, collection: Collection) -> None:
        indexes = collection.list_search_indexes()
        index_names_types = {idx["name"]: idx["type"] for idx in indexes}
        index_names = list(index_names_types.keys())
        index_type = index_names_types.get(self.index_name)
        if self.index_name not in index_names and index_type != "vectorSearch":
            collection.create_search_index(self.__create_index_definition())
            time.sleep(20)

    def __create_index_definition(self) -> SearchIndexModel:
        vector_field: dict = {
            "type": "vector",
            "path": self.index_field,
            "numDimensions": self.number_dimensions,
            "similarity": self.similarity,
        }
        # Bug 2 fix: only include quantization when explicitly set to scalar or binary.
        # Passing null/None causes MongoDB to reject the request.
        if self.quantization and self.quantization != "none":
            vector_field["quantization"] = self.quantization

        fields = [vector_field]
        if self.filter_field:
            fields.append({"type": "filter", "path": self.filter_field})
        return SearchIndexModel(definition={"fields": fields}, name=self.index_name, type="vectorSearch")
