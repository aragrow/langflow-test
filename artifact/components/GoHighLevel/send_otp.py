"""
GHL Send OTP
Generates a 6-digit OTP, stores it in a custom field on the GHL contact,
and triggers a GHL workflow to send the code via email.

Before generating the code, this component verifies that BOTH the email
and phone number provided by the user match the contact record in GHL.
If either doesn't match, the request is rejected — no code is sent and
no appointment data is exposed.

Security Flow:
1. Look up the contact by email.
2. Verify the phone provided by the user matches the phone on file.
3. Only if both match → generate OTP, store it, trigger email workflow.

GHL Setup Required (one-time, manual):
1. Create custom field "otp_code" (text) on Contacts
2. Create custom field "otp_expires_at" (text) on Contacts
3. Create a workflow triggered on otp_code field change that sends an email:
   Subject: "Your verification code"
   Body: "Your verification code is: {{contact.otp_code}}. It expires in 15 minutes."

GHL API:
- POST /contacts/search          — finds the contact by email
- PUT  /contacts/{contactId}     — stores the OTP code and expiry in custom fields

Inputs (configured in Langflow):
- contact_email        : The user's email address (tool_mode)
- contact_phone        : The user's phone number (tool_mode)
- api_key              : GHL Private Integration Token (secret)
- location_id          : GHL Location (Sub-Account) ID
- otp_code_field_key   : Custom field key for the OTP code (e.g. contact.otp_code)
- otp_expiry_field_key : Custom field key for the expiry (e.g. contact.otp_expires_at)

Outputs:
- output            : Message with OTP_SENT or OTP_FAILED status
- component_as_tool : Exposes this component as a tool for agents

Response Prefixes (used by agent prompts to branch logic):
- "OTP_SENT: ..."   — contact verified, code generated and stored; email sent by workflow
- "OTP_FAILED: ..." — email/phone mismatch, contact not found, or API error
"""

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

import httpx
import re
import secrets
from datetime import datetime, timezone, timedelta


GHL_BASE = "https://services.leadconnectorhq.com"
GHL_VERSION = "2021-07-28"

OTP_EXPIRY_MINUTES = 15


class GoHighLevelSendOTP(Component):
    display_name = "GHL Send OTP"
    description = (
        "Verifies the user's email and phone match the GHL contact record, "
        "then generates a 6-digit verification code and stores it on the contact. "
        "A GHL workflow sends the code via email. "
        "Use before allowing appointment queries or changes."
    )
    icon = "shield"
    name = "GoHighLevelSendOTP"

    inputs = [
        MessageTextInput(
            name="contact_email",
            display_name="Email",
            info="The user's email address",
            tool_mode=True,
        ),
        MessageTextInput(
            name="contact_phone",
            display_name="Phone",
            info="The user's phone number",
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
            info="The custom field key in GHL for storing the OTP code (e.g. contact.otp_code)",
        ),
        StrInput(
            name="otp_expiry_field_key",
            display_name="OTP Expiry Field Key",
            info="The custom field key in GHL for storing the OTP expiry timestamp (e.g. contact.otp_expires_at)",
        ),
    ]

    outputs = [
        Output(display_name="OTP Result", name="output", method="send_otp"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Version": GHL_VERSION,
        }

    def _lookup_contact_by_email(self, client: httpx.Client, email: str) -> dict | None:
        """Search GHL contacts by email. Returns first match or None."""
        body = {
            "locationId": self.location_id,
            "pageLimit": 1,
            "filters": [
                {
                    "group": "AND",
                    "filters": [
                        {
                            "field": "email",
                            "operator": "eq",
                            "value": email,
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

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """Strip a phone number to digits only for comparison."""
        return re.sub(r"\D", "", phone)

    def send_otp(self) -> Message:
        """Verify email + phone match the contact, then generate and store OTP.

        Flow:
        1. Look up the contact by email.
        2. Verify the phone the user provided matches the phone on file.
        3. If both match → generate a 6-digit code, store with 15-min expiry.
        4. GHL workflow detects the field change and sends email (external).
        5. Return OTP_SENT with masked email, or OTP_FAILED with reason.
        """
        email = (self.contact_email or "").strip().lower()
        phone = (self.contact_phone or "").strip()

        if not email:
            return self._msg("OTP_FAILED: No email address provided.")
        if not phone:
            return self._msg("OTP_FAILED: No phone number provided.")

        try:
            with httpx.Client(timeout=15.0) as client:
                contact = self._lookup_contact_by_email(client, email)

                if not contact:
                    return self._msg(
                        "OTP_FAILED: The information provided does not match our records."
                    )

                contact_id = contact["id"]
                email_on_file = (contact.get("email") or "").strip().lower()
                phone_on_file = self._normalize_phone(contact.get("phone") or "")
                phone_provided = self._normalize_phone(phone)

                # Verify email matches
                if email != email_on_file:
                    return self._msg(
                        "OTP_FAILED: The information provided does not match our records."
                    )

                # Verify phone matches
                if not phone_on_file:
                    return self._msg(
                        "OTP_FAILED: The information provided does not match our records."
                    )

                if phone_provided != phone_on_file:
                    return self._msg(
                        "OTP_FAILED: The information provided does not match our records."
                    )

                # Both match — generate OTP
                otp_code = f"{secrets.randbelow(900000) + 100000}"
                expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES)
                expires_at_iso = expires_at.isoformat()

                # Store OTP code and expiry on the contact's custom fields
                update_body = {
                    "customFields": [
                        {
                            "key": self.otp_code_field_key,
                            "value": otp_code,
                        },
                        {
                            "key": self.otp_expiry_field_key,
                            "value": expires_at_iso,
                        },
                    ]
                }

                resp = client.put(
                    f"{GHL_BASE}/contacts/{contact_id}",
                    headers=self._headers(),
                    json=update_body,
                )
                resp.raise_for_status()

        except httpx.HTTPStatusError as exc:
            return self._msg(
                f"OTP_FAILED: GHL API error: {exc.response.status_code} — {exc.response.text[:200]}"
            )
        except httpx.TimeoutException:
            return self._msg("OTP_FAILED: GHL API request timed out. Please try again.")

        # Mask the email for the response (show first 2 chars + domain)
        at_idx = email.find("@")
        if at_idx > 2:
            masked = email[:2] + "***" + email[at_idx:]
        elif at_idx > 0:
            masked = email[0] + "***" + email[at_idx:]
        else:
            masked = "***"

        return self._msg(
            f"OTP_SENT: A {OTP_EXPIRY_MINUTES}-minute verification code has been sent "
            f"via email to {masked}. Ask the user to check their inbox and provide the code."
        )

    def _msg(self, text: str) -> Message:
        message = Message(text=text)
        self.status = message
        return message
