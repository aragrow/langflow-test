"""
GHL Verify OTP
Validates a user-provided OTP code against the code stored on the
GHL contact's custom fields. Clears the code after verification
(success or expiry) to prevent reuse.

Security Features:
- Codes are cleared after successful verification (single-use).
- Expired codes are cleared and rejected automatically.
- The stored code is never exposed to the agent or user.

GHL Setup Required (same as Send OTP):
- Custom field "otp_code" (text) on Contacts
- Custom field "otp_expires_at" (text) on Contacts

GHL API:
- POST /contacts/search          — finds the contact by email or phone
- PUT  /contacts/{contactId}     — clears the OTP fields after verification

Inputs (configured in Langflow):
- contact_identifier   : Email or phone to identify the contact (tool_mode)
- otp_code             : The 6-digit code the user received via email (tool_mode)
- api_key              : GHL Private Integration Token (secret)
- location_id          : GHL Location (Sub-Account) ID
- otp_code_field_key   : Custom field key for the stored OTP code
- otp_expiry_field_key : Custom field key for the stored expiry timestamp

Outputs:
- output            : Message with VERIFIED or UNVERIFIED status
- component_as_tool : Exposes this component as a tool for agents

Response Prefixes (used by agent prompts to branch logic):
- "VERIFIED: ..."   — code matched and identity is confirmed
- "UNVERIFIED: ..." — code wrong, expired, or no code was sent
"""

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

import httpx
from datetime import datetime, timezone


GHL_BASE = "https://services.leadconnectorhq.com"
GHL_VERSION = "2021-07-28"


class GoHighLevelVerifyOTP(Component):
    display_name = "GHL Verify OTP"
    description = (
        "Validates a verification code provided by the user against the code "
        "stored on their GHL contact record. Clears the code after verification."
    )
    icon = "shield"
    name = "GoHighLevelVerifyOTP"

    inputs = [
        MessageTextInput(
            name="contact_identifier",
            display_name="Phone or Email",
            info="The contact's phone number or email address",
            tool_mode=True,
        ),
        MessageTextInput(
            name="otp_code",
            display_name="Verification Code",
            info="The 6-digit code the user received via email",
            tool_mode=True,
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
        StrInput(
            name="otp_code_field_key",
            display_name="OTP Code Field Key",
            info="The custom field key in GHL for the stored OTP code (e.g. contact.otp_code)",
        ),
        StrInput(
            name="otp_expiry_field_key",
            display_name="OTP Expiry Field Key",
            info="The custom field key in GHL for the stored OTP expiry (e.g. contact.otp_expires_at)",
        ),
    ]

    outputs = [
        Output(display_name="Verification Result", name="output", method="verify_otp"),
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

    def _clear_otp(self, client: httpx.Client, contact_id: str) -> None:
        """Clear the OTP fields on the contact to prevent reuse."""
        update_body = {
            "customFields": [
                {"key": self.otp_code_field_key, "value": ""},
                {"key": self.otp_expiry_field_key, "value": ""},
            ]
        }
        client.put(
            f"{GHL_BASE}/contacts/{contact_id}",
            headers=self._headers(),
            json=update_body,
        )

    def _get_custom_field(self, contact: dict, field_key: str) -> str:
        """Extract a custom field value from a contact by its key."""
        for field in contact.get("customFields", []):
            if field.get("key") == field_key or field.get("id") == field_key:
                return (field.get("value") or "").strip()
        return ""

    def verify_otp(self) -> Message:
        """Validate the user's OTP code against the stored code.

        Flow:
        1. Look up the contact by email/phone.
        2. Read the stored OTP code and expiry from custom fields.
        3. If no code stored → UNVERIFIED (must send OTP first).
        4. If code expired → clear fields, return UNVERIFIED.
        5. If code matches → clear fields, return VERIFIED.
        6. If code doesn't match → return UNVERIFIED (fields kept for retry).
        """
        identifier = (self.contact_identifier or "").strip()
        user_code = (self.otp_code or "").strip()

        if not identifier:
            return self._msg("UNVERIFIED: No contact identifier provided.")
        if not user_code:
            return self._msg("UNVERIFIED: No verification code provided.")

        try:
            with httpx.Client(timeout=15.0) as client:
                contact = self._lookup_contact(client)

                if not contact:
                    return self._msg(
                        f"UNVERIFIED: No contact found matching '{identifier}'."
                    )

                contact_id = contact["id"]
                contact_name = (
                    f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()
                    or identifier
                )

                # Read stored OTP and expiry from custom fields
                stored_code = self._get_custom_field(contact, self.otp_code_field_key)
                stored_expiry = self._get_custom_field(contact, self.otp_expiry_field_key)

                if not stored_code:
                    return self._msg(
                        "UNVERIFIED: No verification code was sent for this contact. "
                        "Please request a new code first."
                    )

                # Check expiry
                if stored_expiry:
                    try:
                        expiry_dt = datetime.fromisoformat(stored_expiry.replace("Z", "+00:00"))
                        if datetime.now(timezone.utc) > expiry_dt:
                            self._clear_otp(client, contact_id)
                            return self._msg(
                                "UNVERIFIED: The verification code has expired. "
                                "Please request a new code."
                            )
                    except (ValueError, AttributeError):
                        pass  # If expiry can't be parsed, skip the check

                # Compare codes
                if user_code == stored_code:
                    self._clear_otp(client, contact_id)
                    return self._msg(
                        f"VERIFIED: Identity confirmed for {contact_name}. "
                        "You may proceed with appointment queries or changes."
                    )
                else:
                    return self._msg(
                        "UNVERIFIED: The code does not match. "
                        "Please double-check and try again, or request a new code."
                    )

        except httpx.HTTPStatusError as exc:
            return self._msg(
                f"UNVERIFIED: GHL API error: {exc.response.status_code} — {exc.response.text[:200]}"
            )
        except httpx.TimeoutException:
            return self._msg("UNVERIFIED: GHL API request timed out. Please try again.")

    def _msg(self, text: str) -> Message:
        message = Message(text=text)
        self.status = message
        return message
