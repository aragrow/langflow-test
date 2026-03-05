from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

import httpx
from datetime import datetime, timezone


GHL_API_BASE = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"


class GoHighLevelCalendarLookup(Component):
    display_name = "GHL Calendar Lookup"
    description = (
        "Retrieves upcoming appointments for a GoHighLevel contact by their contact ID. "
        "Returns appointment details including title, date, time, status, and calendar name."
    )
    icon = "calendar"
    name = "GoHighLevelCalendarLookup"

    inputs = [
        MessageTextInput(
            name="contact_id",
            display_name="Contact ID",
            info="The GoHighLevel contact ID to look up appointments for",
            tool_mode=True,
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
        IntInput(
            name="max_results",
            display_name="Max Results",
            info="Maximum number of appointments to return",
            value=5,
        ),
    ]

    outputs = [
        Output(display_name="Appointments", name="output", method="build_output"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Version": GHL_API_VERSION,
        }

    def _get_appointments(self, client: httpx.Client) -> list:
        response = client.get(
            f"{GHL_API_BASE}/contacts/{self.contact_id}/appointments",
            headers=self._headers(),
        )
        response.raise_for_status()
        data = response.json()
        return data.get("events", [])

    def _get_calendars(self, client: httpx.Client) -> dict:
        """Returns a map of calendarId -> calendar name."""
        response = client.get(
            f"{GHL_API_BASE}/calendars/",
            headers=self._headers(),
            params={"locationId": self.location_id},
        )
        response.raise_for_status()
        data = response.json()
        return {c["id"]: c.get("name", "Unknown Calendar") for c in data.get("calendars", [])}

    def _format_appointment(self, appt: dict, calendar_name: str) -> str:
        start = appt.get("startTime", "")
        end = appt.get("endTime", "")
        try:
            start_fmt = datetime.fromisoformat(start.replace("Z", "+00:00")).strftime("%b %d, %Y %I:%M %p UTC")
            end_fmt = datetime.fromisoformat(end.replace("Z", "+00:00")).strftime("%I:%M %p UTC")
        except (ValueError, AttributeError):
            start_fmt = start
            end_fmt = end

        return (
            f"- {appt.get('title', 'Appointment')} | {calendar_name} | "
            f"{start_fmt} – {end_fmt} | Status: {appt.get('appointmentStatus', 'unknown')}"
        )

    def build_output(self) -> Message:
        with httpx.Client(timeout=10.0) as client:
            appointments = self._get_appointments(client)
            calendar_map = self._get_calendars(client)

        if not appointments:
            text = f"No appointments found for contact ID: {self.contact_id}"
            message = Message(text=text)
            self.status = message
            return message

        now = datetime.now(timezone.utc)
        upcoming = [
            a for a in appointments
            if datetime.fromisoformat(a.get("startTime", "").replace("Z", "+00:00")) >= now
        ]
        upcoming.sort(key=lambda a: a.get("startTime", ""))
        upcoming = upcoming[: self.max_results]

        if not upcoming:
            text = f"No upcoming appointments found for contact ID: {self.contact_id}"
            message = Message(text=text)
            self.status = message
            return message

        lines = [f"Upcoming appointments ({len(upcoming)}):"]
        for appt in upcoming:
            calendar_name = calendar_map.get(appt.get("calendarId", ""), "Unknown Calendar")
            lines.append(self._format_appointment(appt, calendar_name))

        text = "\n".join(lines)
        message = Message(text=text)
        self.status = message
        return message
