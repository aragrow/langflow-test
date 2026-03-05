from lfx.custom.custom_component.component import Component
from lfx.io import DropdownInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

import httpx


GHL_API_BASE = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"


class GoHighLevelContactLookup(Component):
    display_name = "GHL Contact Lookup"
    description = (
        "Searches GoHighLevel by email or phone number to retrieve the contact's "
        "classification custom field and determine if the caller is a vendor, client, or prospect."
    )
    icon = "search"
    name = "GoHighLevelContactLookup"

    inputs = [
        MessageTextInput(
            name="search_value",
            display_name="Email or Phone",
            info="The email address or phone number to search for in GoHighLevel",
            tool_mode=True,
        ),
        DropdownInput(
            name="search_type",
            display_name="Search By",
            options=["email", "phone"],
            value="email",
            info="Whether to search by email or phone number",
        ),
        SecretStrInput(
            name="api_key",
            display_name="GHL API Key",
            info="Your GoHighLevel Private Integration Token or OAuth Bearer token",
        ),
        StrInput(
            name="location_id",
            display_name="Location ID",
            info="Your GoHighLevel Location (Sub-Account) ID",
        ),
        StrInput(
            name="classification_field_id",
            display_name="Classification Field ID",
            info="The custom field ID in GHL that holds the classification (vendor/client/prospect). "
                 "Find it under Settings > Custom Fields in GHL.",
        ),
    ]

    outputs = [
        Output(display_name="Classification", name="output", method="build_output"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    def _build_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Version": GHL_API_VERSION,
        }

    def _search_contact(self, client: httpx.Client) -> dict | None:
        """Search for a contact using POST /contacts/search with advanced filters."""
        body = {
            "locationId": self.location_id,
            "pageLimit": 1,
            "filters": [
                {
                    "group": "AND",
                    "filters": [
                        {
                            "field": self.search_type,
                            "operator": "eq",
                            "value": self.search_value,
                        }
                    ],
                }
            ],
        }

        response = client.post(
            f"{GHL_API_BASE}/contacts/search",
            headers=self._build_headers(),
            json=body,
        )
        response.raise_for_status()
        data = response.json()
        contacts = data.get("contacts", [])
        return contacts[0] if contacts else None

    def _get_classification(self, contact: dict) -> str:
        """Extract the classification value from customFields using the field ID."""
        custom_fields = contact.get("customFields", [])
        for field in custom_fields:
            if field.get("id") == self.classification_field_id:
                value = field.get("value", "")
                return value.strip().lower() if value else "unknown"
        return "unknown"

    def build_output(self) -> Message:
        with httpx.Client(timeout=10.0) as client:
            contact = self._search_contact(client)

        if not contact:
            text = f"No contact found in GoHighLevel for {self.search_type}: {self.search_value}"
            message = Message(text=text)
            self.status = message
            return message

        classification = self._get_classification(contact)
        contact_name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()

        text = (
            f"Contact found: {contact_name} (ID: {contact.get('id')}). "
            f"Classification: {classification}"
        )
        message = Message(text=text)
        self.status = message
        return message
