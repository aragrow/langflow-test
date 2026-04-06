"""
GHL Get Appointments
Retrieves upcoming booked appointments for a contact from GoHighLevel.
Uses the calendar events endpoint filtered by contactId.

Filters out cancelled appointments and returns only active ones
(status: confirmed, new, or showed) sorted by start time.

GHL API:
- POST /contacts/search                  — finds the contact by email or phone
- GET  /calendars/events/appointments    — fetches events filtered by contactId,
      calendarId, and date range (next 90 days)

Timezone Handling:
- timezone      : Organization timezone for GHL API calls
- user_timezone : User's timezone for displaying appointment times.
                  Falls back to org timezone if empty.

Inputs (configured in Langflow):
- contact_identifier : Email or phone to identify the contact (tool_mode)
- api_key            : GHL Private Integration Token (secret)
- location_id        : GHL Location (Sub-Account) ID
- calendar_id        : GHL Calendar ID to query
- timezone           : Organization timezone (e.g. America/Chicago)
- user_timezone      : User's timezone for display (tool_mode, optional)

Outputs:
- output            : Message listing upcoming appointments with details
- component_as_tool : Exposes this component as a tool for agents
"""

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

import httpx
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


GHL_BASE = "https://services.leadconnectorhq.com"
GHL_VERSION = "2021-07-28"


class GoHighLevelGetAppointments(Component):
    display_name = "GHL Get Appointments"
    description = (
        "Retrieves upcoming booked appointments for a contact from GoHighLevel. "
        "Pass the contact's email or phone to look up their scheduled events."
    )
    icon = "calendar"
    name = "GoHighLevelGetAppointments"

    inputs = [
        MessageTextInput(
            name="contact_identifier",
            display_name="Phone or Email",
            info="The contact's phone number or email address",
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
            name="calendar_id",
            display_name="Calendar ID",
            info="GoHighLevel Calendar ID to query appointments from",
        ),
        StrInput(
            name="timezone",
            display_name="Organization Timezone",
            info="Organization timezone for GHL API calls (e.g. America/Chicago)",
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
        Output(display_name="Appointments", name="output", method="get_appointments"),
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

    def _format_time_for_user(self, iso_str: str) -> str:
        """Convert ISO datetime to human-readable format in the user's timezone."""
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            user_tz_name = (self.user_timezone or "").strip() or self.timezone or "UTC"
            dt_user = dt.astimezone(ZoneInfo(user_tz_name))
            return dt_user.strftime("%I:%M %p on %A, %B %d, %Y")
        except (ValueError, AttributeError, KeyError):
            return iso_str

    def get_appointments(self) -> Message:
        """Retrieve and display upcoming appointments for a contact.

        Flow:
        1. Look up the contact by email/phone.
        2. Query GHL for appointments in the next 90 days filtered by
           contactId, calendarId, and locationId.
        3. Filter to active statuses (confirmed, new, showed).
        4. Sort by start time and format in the user's timezone.
        """
        identifier = (self.contact_identifier or "").strip()
        if not identifier:
            return self._msg("Error: A phone number or email address is required.")

        try:
            with httpx.Client(timeout=15.0) as client:
                contact = self._lookup_contact(client)

                if not contact:
                    return self._msg(
                        f"Could not find a contact matching '{identifier}'. "
                        "Please verify the email or phone number."
                    )

                contact_id = contact["id"]
                first = contact.get("firstName", "")
                last = contact.get("lastName", "")
                contact_name = f"{first} {last}".strip() or identifier

                # Fetch appointments for this contact. GHL's
                # /contacts/{id}/appointments endpoint returns every
                # appointment linked to the contact with no query params
                # required — we filter by calendar/date client-side.
                resp = client.get(
                    f"{GHL_BASE}/contacts/{contact_id}/appointments",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()

        except httpx.HTTPStatusError as exc:
            return self._msg(
                f"GHL API error: {exc.response.status_code} — {exc.response.text[:200]}"
            )
        except httpx.TimeoutException:
            return self._msg("GHL API request timed out. Please try again.")

        events = data.get("events", [])

        # The contact appointments endpoint returns every event for the
        # contact across all calendars, past and future. Filter down to:
        # - matching the configured calendar (if any)
        # - active statuses (confirmed / new / showed — exclude cancelled)
        # - upcoming only (startTime in the future)
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Treat obvious placeholder values (anything with a space or too
        # long/short to be a real GHL id) as "no calendar filter".
        cal_filter = (self.calendar_id or "").strip()
        if " " in cal_filter or not (15 <= len(cal_filter) <= 30):
            cal_filter = ""

        active_events = [
            e for e in events
            if e.get("appointmentStatus", "").lower() in ("confirmed", "new", "showed")
            and (not cal_filter or e.get("calendarId") == cal_filter)
            and (e.get("startTime", "") >= now_iso)
        ]

        if not active_events:
            # Build a diagnostic so we know why the filter rejected things.
            total = len(events)
            by_status: dict[str, int] = {}
            calendars_seen: set[str] = set()
            for e in events:
                s = (e.get("appointmentStatus") or "").lower() or "(none)"
                by_status[s] = by_status.get(s, 0) + 1
                c = e.get("calendarId") or ""
                if c:
                    calendars_seen.add(c)
            return self._msg(
                f"No upcoming appointments found for {contact_name} "
                f"(raw events: {total}, statuses: {by_status}, "
                f"calendars seen: {sorted(calendars_seen)}, "
                f"calendar_filter: {cal_filter or '(disabled)'!r}, "
                f"now: {now_iso}). "
                "Would you like to schedule a new one?"
            )

        # Sort by start time
        active_events.sort(key=lambda e: e.get("startTime", ""))

        lines = []
        for i, event in enumerate(active_events, 1):
            title = event.get("title", "Appointment")
            start = self._format_time_for_user(event.get("startTime", ""))
            status = event.get("appointmentStatus", "unknown")
            event_id = event.get("id", "unknown")
            lines.append(
                f"  {i}. [EVENT_ID: {event_id}]\n"
                f"     Service: {title}\n"
                f"     Time: {start}\n"
                f"     Status: {status}"
            )

        appointments_text = "\n".join(lines)
        return self._msg(
            f"Upcoming appointments for {contact_name}:\n\n"
            f"{appointments_text}\n\n"
            "How can I help with these?"
        )

    def _msg(self, text: str) -> Message:
        message = Message(text=text)
        self.status = message
        return message
