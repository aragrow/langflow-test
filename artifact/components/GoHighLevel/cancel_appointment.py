"""
GHL Cancel Appointment
Cancels (or deletes) an existing appointment in GoHighLevel by event ID.
Supports both status-change (mark as cancelled) and full deletion.

Security: Verifies that the appointment belongs to the contact before
cancelling — prevents a user from cancelling someone else's appointment.

GHL API:
- POST   /contacts/search                         — finds the contact
- GET    /calendars/events/appointments/{eventId}  — fetches event for ownership check
- PUT    /calendars/events/appointments/{eventId}  — marks as cancelled (preserves record)
- DELETE /calendars/events/appointments/{eventId}  — deletes entirely

Timezone Handling:
- org_timezone  : Organization timezone (config, not sent to API here)
- user_timezone : User's timezone for displaying the cancelled appointment time

Inputs (configured in Langflow):
- event_id             : The GHL event/appointment ID to cancel (tool_mode)
- contact_identifier   : Email or phone for ownership verification (tool_mode)
- cancel_mode          : "mark_cancelled" or "delete" (dropdown)
- api_key              : GHL Private Integration Token (secret)
- location_id          : GHL Location (Sub-Account) ID
- org_timezone         : Organization timezone
- user_timezone        : User's timezone for display (tool_mode, optional)

Outputs:
- output            : Message confirming the cancellation
- component_as_tool : Exposes this component as a tool for agents
"""

from lfx.custom.custom_component.component import Component
from lfx.io import DropdownInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

import httpx
from datetime import datetime
from zoneinfo import ZoneInfo


GHL_BASE = "https://services.leadconnectorhq.com"
GHL_VERSION = "2021-07-28"


class GoHighLevelCancelAppointment(Component):
    display_name = "GHL Cancel Appointment"
    description = (
        "Cancels an existing appointment in GoHighLevel by event ID. "
        "Can either mark as cancelled (preserves record) or delete entirely."
    )
    icon = "calendar"
    name = "GoHighLevelCancelAppointment"

    inputs = [
        MessageTextInput(
            name="event_id",
            display_name="Event ID",
            info="The GoHighLevel event/appointment ID to cancel",
            tool_mode=True,
        ),
        MessageTextInput(
            name="contact_identifier",
            display_name="Phone or Email",
            info="The contact's phone number or email address (for verification)",
            tool_mode=True,
        ),
        DropdownInput(
            name="cancel_mode",
            display_name="Cancel Mode",
            options=["mark_cancelled", "delete"],
            value="mark_cancelled",
            info="'mark_cancelled' sets status to cancelled (keeps record). 'delete' removes it entirely.",
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
            name="org_timezone",
            display_name="Organization Timezone",
            info="Organization timezone (e.g. America/Chicago)",
            value="America/Chicago",
        ),
        MessageTextInput(
            name="user_timezone",
            display_name="User Timezone",
            info="The user's timezone for display (e.g. America/New_York). If empty, defaults to the organization timezone.",
            tool_mode=True,
        ),
    ]

    outputs = [
        Output(display_name="Cancel Result", name="output", method="cancel_appointment"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Version": GHL_VERSION,
        }

    def _lookup_contact(self, client: httpx.Client) -> dict | None:
        """Search GHL contacts by email or phone. Returns first match or None."""
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

    def _get_event(self, client: httpx.Client, event_id: str) -> dict | None:
        """Fetch a single event by ID to verify ownership."""
        resp = client.get(
            f"{GHL_BASE}/calendars/events/appointments/{event_id}",
            headers=self._headers(),
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def _format_time_for_user(self, iso_str: str) -> str:
        """Convert ISO time to human-readable format in the user's timezone."""
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            user_tz_name = (self.user_timezone or "").strip() or self.org_timezone or "UTC"
            dt_user = dt.astimezone(ZoneInfo(user_tz_name))
            return dt_user.strftime("%I:%M %p on %A, %B %d, %Y")
        except (ValueError, AttributeError, KeyError):
            return iso_str

    def cancel_appointment(self) -> Message:
        """Cancel or delete an appointment after verifying contact ownership.

        Flow:
        1. Look up the contact by email/phone to get their contactId.
        2. Fetch the event by ID and verify its contactId matches.
        3. Cancel (status change) or delete based on cancel_mode.
        4. Return confirmation with the appointment details in user's timezone.
        """
        event_id = (self.event_id or "").strip()
        identifier = (self.contact_identifier or "").strip()

        if not event_id:
            return self._msg("Error: An event ID is required. Use 'Get Appointments' first to find the ID.")
        if not identifier:
            return self._msg("Error: A phone number or email address is required for verification.")

        try:
            with httpx.Client(timeout=15.0) as client:
                # Step 1: Look up the contact to get their contactId
                contact = self._lookup_contact(client)
                if not contact:
                    return self._msg(
                        f"Could not find a contact matching '{identifier}'. "
                        "Please verify the email or phone number."
                    )

                contact_id = contact["id"]
                contact_name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip() or identifier

                # Step 2: Fetch the event and verify it belongs to this contact
                event = self._get_event(client, event_id)
                if not event:
                    return self._msg(
                        f"No appointment found with ID '{event_id}'. "
                        "It may have already been cancelled or deleted."
                    )

                event_contact_id = event.get("contactId", "")
                if event_contact_id != contact_id:
                    return self._msg(
                        "Verification failed: this appointment does not belong to the contact "
                        f"'{identifier}'. Cannot cancel another person's appointment."
                    )

                title = event.get("title", "Appointment")
                start_time = self._format_time_for_user(event.get("startTime", ""))

                # Step 3: Cancel or delete
                if self.cancel_mode == "delete":
                    resp = client.delete(
                        f"{GHL_BASE}/calendars/events/appointments/{event_id}",
                        headers=self._headers(),
                    )
                    resp.raise_for_status()
                    action = "deleted"
                else:
                    resp = client.put(
                        f"{GHL_BASE}/calendars/events/appointments/{event_id}",
                        headers=self._headers(),
                        json={"appointmentStatus": "cancelled"},
                    )
                    resp.raise_for_status()
                    action = "cancelled"

        except httpx.HTTPStatusError as exc:
            return self._msg(
                f"GHL API error: {exc.response.status_code} — {exc.response.text[:200]}"
            )
        except httpx.TimeoutException:
            return self._msg("GHL API request timed out. Please try again.")

        return self._msg(
            f"Appointment successfully {action} for {contact_name}.\n"
            f"  Service: {title}\n"
            f"  Was scheduled: {start_time}\n"
            f"  Event ID: {event_id}"
        )

    def _msg(self, text: str) -> Message:
        message = Message(text=text)
        self.status = message
        return message
