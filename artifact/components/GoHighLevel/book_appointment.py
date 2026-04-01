"""
GHL Book Appointment
Books a confirmed appointment in GoHighLevel after the user selects a slot.
"""

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

import httpx
from datetime import datetime, timedelta


GHL_BASE = "https://services.leadconnectorhq.com"
GHL_VERSION = "2021-07-28"


class GoHighLevelBookAppointment(Component):
    display_name = "GHL Book Appointment"
    description = (
        "Books a confirmed appointment in GoHighLevel. "
        "Pass the contact's email or phone, the selected slot time, and the service name."
    )
    icon = "calendar"
    name = "GoHighLevelBookAppointment"

    inputs = [
        MessageTextInput(
            name="contact_identifier",
            display_name="Phone or Email",
            info="The contact's phone number or email address",
            tool_mode=True,
        ),
        MessageTextInput(
            name="selected_slot",
            display_name="Selected Slot",
            info="The selected slot in ISO format, e.g. 2026-04-03T10:00:00-04:00",
            tool_mode=True,
        ),
        MessageTextInput(
            name="service_name",
            display_name="Service Name",
            info="The service or appointment title, e.g. WordPress Optimization",
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
            info="GoHighLevel Calendar ID to book on",
        ),
        StrInput(
            name="slot_duration_minutes",
            display_name="Slot Duration (minutes)",
            info="Duration of the appointment in minutes",
            value="30",
        ),
    ]

    outputs = [
        Output(display_name="Booking Result", name="output", method="book_appointment"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Version": GHL_VERSION,
        }

    def _lookup_contact(self, client: httpx.Client) -> dict | None:
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

    @staticmethod
    def _format_time(iso_str: str) -> str:
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return dt.strftime("%I:%M %p on %A, %B %d, %Y")
        except (ValueError, AttributeError):
            return iso_str

    def book_appointment(self) -> Message:
        identifier = (self.contact_identifier or "").strip()
        slot = (self.selected_slot or "").strip()
        service = (self.service_name or "").strip()

        if not identifier:
            return self._msg("Error: A phone number or email address is required.")
        if not slot:
            return self._msg("Error: No slot was selected. Please choose a time slot first.")
        if not service:
            return self._msg("Error: A service name is required.")

        try:
            duration = int(self.slot_duration_minutes or "30")
        except ValueError:
            duration = 30

        try:
            start_dt = datetime.fromisoformat(slot.replace("Z", "+00:00"))
            end_dt = start_dt + timedelta(minutes=duration)
            start_iso = start_dt.isoformat()
            end_iso = end_dt.isoformat()
        except (ValueError, AttributeError):
            return self._msg(
                f"Error: Could not parse the selected slot time '{slot}'. "
                "Expected ISO format like 2026-04-03T10:00:00-04:00."
            )

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

                appointment_body = {
                    "calendarId": self.calendar_id,
                    "locationId": self.location_id,
                    "contactId": contact_id,
                    "startTime": start_iso,
                    "endTime": end_iso,
                    "title": service,
                    "appointmentStatus": "confirmed",
                }

                resp = client.post(
                    f"{GHL_BASE}/calendars/events/appointments",
                    headers=self._headers(),
                    json=appointment_body,
                )
                resp.raise_for_status()
                result = resp.json()

        except httpx.HTTPStatusError as exc:
            return self._msg(
                f"GHL API error: {exc.response.status_code} — {exc.response.text[:200]}"
            )
        except httpx.TimeoutException:
            return self._msg("GHL API request timed out. Please try again.")

        appointment_id = result.get("id", "unknown")
        time_display = self._format_time(start_iso)

        return self._msg(
            f"Appointment booked successfully for {contact_name}!\n"
            f"  Service: {service}\n"
            f"  Time: {time_display}\n"
            f"  Confirmation ID: {appointment_id}\n"
            "You will receive a confirmation shortly."
        )

    def _msg(self, text: str) -> Message:
        message = Message(text=text)
        self.status = message
        return message
