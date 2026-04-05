"""
GHL Contact Lookup
Searches GoHighLevel contacts by email or phone number and returns
the contact's name, id, and classification (vendor/client/prospect)
for downstream routing.

GHL API:
- POST /contacts/search — finds the contact by email or phone
- Reads the classification from a hard-coded custom field ID

Inputs (configured in Langflow):
- search_value : The email or phone to search for (tool_mode)
- search_type  : "email" or "phone" (dropdown)
- api_key      : GHL Private Integration Token (secret)
- location_id  : GHL Location (Sub-Account) ID

Outputs:
- output            : Message with contact name, id, and classification
- component_as_tool : Exposes this component as a tool for agents
"""

from lfx.custom.custom_component.component import Component
from lfx.io import DropdownInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

import httpx


GHL_BASE = "https://services.leadconnectorhq.com"
GHL_VERSION = "2021-07-28"

# Custom field ID in GHL that stores the contact classification
# (vendor / client / prospect). Update this if the field is recreated in GHL.
# Find it under Settings > Custom Fields in GHL.
CLASSIFICATION_FIELD_ID = ""  # TODO: set to your GHL custom field id


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
    ]

    outputs = [
        Output(display_name="Classification", name="output", method="lookup_contact"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    # ── API helpers ─────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Version": GHL_VERSION,
        }

    def _search_contact(self, client: httpx.Client) -> dict | None:
        """Search GHL contacts by email or phone. Returns first match or None."""
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
        resp = client.post(
            f"{GHL_BASE}/contacts/search",
            headers=self._headers(),
            json=body,
        )
        resp.raise_for_status()
        contacts = resp.json().get("contacts", [])
        return contacts[0] if contacts else None

    def _get_classification(self, contact: dict) -> str:
        """Extract the classification value from customFields using the field ID."""
        if not CLASSIFICATION_FIELD_ID:
            return "unknown"
        for field in contact.get("customFields", []):
            if field.get("id") == CLASSIFICATION_FIELD_ID:
                value = field.get("value", "")
                return value.strip().lower() if value else "unknown"
        return "unknown"

    # ── Main output ─────────────────────────────────────────────────

    def lookup_contact(self) -> Message:
        """Look up a contact and return their classification.

        Flow:
        1. Search for the contact by email or phone via POST /contacts/search.
        2. Extract the classification value from the configured custom field.
        3. Return a message with the contact name, ID, and classification.
        """
        identifier = (self.search_value or "").strip()
        if not identifier:
            return self._msg("Error: An email address or phone number is required.")

        try:
            with httpx.Client(timeout=10.0) as client:
                contact = self._search_contact(client)
        except httpx.HTTPStatusError as exc:
            return self._msg(
                f"GHL API error: {exc.response.status_code} — {exc.response.text[:200]}"
            )
        except httpx.TimeoutException:
            return self._msg("GHL API request timed out. Please try again.")

        if not contact:
            return self._msg(
                f"No contact found in GoHighLevel for {self.search_type}: {identifier}"
            )

        classification = self._get_classification(contact)
        first = contact.get("firstName", "")
        last = contact.get("lastName", "")
        contact_name = f"{first} {last}".strip() or identifier

        return self._msg(
            f"Contact found: {contact_name} (ID: {contact.get('id')}). "
            f"Classification: {classification}"
        )

    def _msg(self, text: str) -> Message:
        message = Message(text=text)
        self.status = message
        return message
