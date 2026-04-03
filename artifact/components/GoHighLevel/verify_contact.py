"""
GHL Verify Contact (Challenge-Based — Lightweight)
Verifies a caller's identity by challenging them to confirm details
from their GHL contact record (last 4 of phone, zip code, or last name).

NOTE: This is a lightweight fallback verification method. For stronger
security, use GHL Send OTP + GHL Verify OTP which sends a one-time
code via SMS. This component is kept for cases where OTP is not available
(e.g. no phone on file, GHL workflow not configured yet).

GHL API:
- POST /contacts/search — finds the contact by email or phone

Inputs (configured in Langflow):
- contact_identifier : Email or phone to look up (tool_mode)
- challenge_answer   : The user's answer to the challenge (tool_mode)
- challenge_type     : "last_4_phone", "zip_code", or "last_name" (dropdown)
- api_key            : GHL Private Integration Token (secret)
- location_id        : GHL Location (Sub-Account) ID

Outputs:
- output            : Message with VERIFIED or UNVERIFIED status
- component_as_tool : Exposes this component as a tool for agents

Response Prefixes (used by agent prompts to branch logic):
- "VERIFIED: ..."   — challenge answer matched the contact record
- "UNVERIFIED: ..." — answer didn't match, contact not found, or error
"""

from lfx.custom.custom_component.component import Component
from lfx.io import DropdownInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

import httpx


GHL_BASE = "https://services.leadconnectorhq.com"
GHL_VERSION = "2021-07-28"


class GoHighLevelVerifyContact(Component):
    display_name = "GHL Verify Contact"
    description = (
        "Verifies a contact's identity by checking a challenge answer "
        "(last 4 digits of phone, zip code, or last name) against their "
        "GoHighLevel contact record. Use before allowing appointment changes."
    )
    icon = "shield"
    name = "GoHighLevelVerifyContact"

    inputs = [
        MessageTextInput(
            name="contact_identifier",
            display_name="Phone or Email",
            info="The contact's phone number or email address to look up",
            tool_mode=True,
        ),
        MessageTextInput(
            name="challenge_answer",
            display_name="Challenge Answer",
            info="The user's answer to the verification challenge",
            tool_mode=True,
        ),
        DropdownInput(
            name="challenge_type",
            display_name="Challenge Type",
            options=["last_4_phone", "zip_code", "last_name"],
            value="last_4_phone",
            info="Which piece of information to verify against",
        ),
        SecretStrInput(
            name="api_key",
            display_name="GHL API Key",
            info="GoHighLevel Private Integration Token",
        ),
        StrInput(
            name="location_id",
            display_name="Location ID",
            info="GoHighLevel Location (Sub-Account) ID",
        ),
    ]

    outputs = [
        Output(display_name="Verification Result", name="output", method="verify_contact"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Version": GHL_VERSION,
        }

    def _lookup_contact(self, client: httpx.Client) -> dict | None:
        """Search GHL contacts by email or phone."""
        identifier = self.contact_identifier.strip()
        search_field = "email" if "@" in identifier else "phone"

        body = {
            "locationId": self.location_id,
            "pageLimit": 1,
            "filters": [
                {
                    "group": "AND",
                    "filters": [
                        {
                            "field": search_field,
                            "operator": "eq",
                            "value": identifier,
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

    def verify_contact(self) -> Message:
        """Verify a contact's identity using a challenge-response check.

        Flow:
        1. Look up the contact by email/phone.
        2. Based on challenge_type, extract the expected value from the
           contact record (last 4 digits of phone, zip code, or last name).
        3. Compare against the user's answer (case-insensitive, digits-only
           for phone).
        4. Return VERIFIED or UNVERIFIED.
        """
        identifier = (self.contact_identifier or "").strip()
        answer = (self.challenge_answer or "").strip()

        if not identifier:
            return self._msg("UNVERIFIED: No contact identifier provided.")
        if not answer:
            return self._msg("UNVERIFIED: No challenge answer provided.")

        try:
            with httpx.Client(timeout=15.0) as client:
                contact = self._lookup_contact(client)
        except httpx.HTTPStatusError as exc:
            return self._msg(
                f"UNVERIFIED: GHL API error: {exc.response.status_code} — {exc.response.text[:200]}"
            )
        except httpx.TimeoutException:
            return self._msg("UNVERIFIED: GHL API request timed out. Please try again.")

        if not contact:
            return self._msg(
                f"UNVERIFIED: No contact found matching '{identifier}'."
            )

        contact_name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip() or identifier

        # Perform the challenge check
        if self.challenge_type == "last_4_phone":
            phone = contact.get("phone", "")
            # Strip non-digits for comparison
            phone_digits = "".join(c for c in phone if c.isdigit())
            answer_digits = "".join(c for c in answer if c.isdigit())
            if len(phone_digits) < 4:
                return self._msg(
                    "UNVERIFIED: Contact does not have a valid phone number on file."
                )
            if phone_digits[-4:] == answer_digits:
                return self._msg(
                    f"VERIFIED: Identity confirmed for {contact_name}. "
                    "You may proceed with appointment queries or changes."
                )
            else:
                return self._msg(
                    "UNVERIFIED: The last 4 digits do not match our records. "
                    "Please double-check and try again."
                )

        elif self.challenge_type == "zip_code":
            postal = contact.get("postalCode", "") or contact.get("address", {}).get("postalCode", "")
            if not postal:
                return self._msg(
                    "UNVERIFIED: No zip code on file for this contact."
                )
            if postal.strip().lower() == answer.lower():
                return self._msg(
                    f"VERIFIED: Identity confirmed for {contact_name}. "
                    "You may proceed with appointment queries or changes."
                )
            else:
                return self._msg(
                    "UNVERIFIED: The zip code does not match our records. "
                    "Please double-check and try again."
                )

        elif self.challenge_type == "last_name":
            last_name = contact.get("lastName", "")
            if not last_name:
                return self._msg(
                    "UNVERIFIED: No last name on file for this contact."
                )
            if last_name.strip().lower() == answer.lower():
                return self._msg(
                    f"VERIFIED: Identity confirmed for {contact_name}. "
                    "You may proceed with appointment queries or changes."
                )
            else:
                return self._msg(
                    "UNVERIFIED: The last name does not match our records. "
                    "Please double-check and try again."
                )

        return self._msg("UNVERIFIED: Unknown challenge type.")

    def _msg(self, text: str) -> Message:
        message = Message(text=text)
        self.status = message
        return message
