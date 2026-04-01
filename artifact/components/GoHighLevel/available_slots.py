"""
GHL Available Slots V2
Retrieves available calendar slots from GoHighLevel.
Walks forward up to 14 business days to find availability.

Key differences from V1:
- locationId is NEVER sent as a query param to free-slots (causes 422)
- Walks forward up to 14 business days instead of checking only the next day
- Better error handling with descriptive messages returned to the agent
"""

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

import httpx
from datetime import datetime, timezone, timedelta


GHL_BASE = "https://services.leadconnectorhq.com"
GHL_VERSION = "2021-07-28"


class GoHighLevelAvailableSlots(Component):
    display_name = "GHL Available Slots"
    description = (
        "Retrieves available calendar slots from GoHighLevel. "
        "Automatically walks forward up to 14 business days to find open slots. "
        "Pass a phone number or email to identify the contact."
    )
    icon = "calendar"
    name = "GoHighLevelAvailableSlots"

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
            info="GoHighLevel Calendar ID to check for slots",
        ),
        StrInput(
            name="timezone",
            display_name="Timezone",
            info="Timezone for slot display (e.g. America/New_York)",
            value="America/Chicago",
        ),
    ]

    outputs = [
        Output(display_name="Available Slots", name="output", method="get_available_slots"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    # ── API helpers ─────────────────────────────────────────────────

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

    def _get_free_slots_for_day(self, client: httpx.Client, day: datetime) -> list[str]:
        """Fetch free slots for a single calendar day.

        IMPORTANT: Do NOT pass locationId as a query param — GHL returns 422.
        locationId is only used in the contacts/search POST body.
        """
        start_ms = int(day.timestamp() * 1000)
        end_of_day = day.replace(hour=23, minute=59, second=59)
        end_ms = int(end_of_day.timestamp() * 1000)

        params: dict = {
            "startDate": start_ms,
            "endDate": end_ms,
        }
        if self.timezone:
            params["timezone"] = self.timezone

        resp = client.get(
            f"{GHL_BASE}/calendars/{self.calendar_id}/free-slots",
            headers=self._headers(),
            params=params,
        )
        resp.raise_for_status()

        data = resp.json()
        date_key = day.strftime("%Y-%m-%d")
        return data.get(date_key, {}).get("slots", [])

    def _find_next_available_day(
        self, client: httpx.Client, start: datetime, max_days: int = 14
    ) -> tuple[datetime, list[str]]:
        """Walk forward from start, skipping weekends, until slots are found."""
        candidate = start
        for _ in range(max_days):
            slots = self._get_free_slots_for_day(client, candidate)
            if slots:
                return candidate, slots
            candidate += timedelta(days=1)
            while candidate.weekday() >= 5:
                candidate += timedelta(days=1)
        return start, []

    @staticmethod
    def _next_business_day() -> datetime:
        """Return midnight UTC of the next weekday."""
        today = datetime.now(timezone.utc)
        nxt = today + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt.replace(hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def _format_time(slot_iso: str) -> str:
        """Convert '2026-04-03T10:00:00-04:00' → '10:00 AM'."""
        try:
            dt = datetime.fromisoformat(slot_iso.replace("Z", "+00:00"))
            return dt.strftime("%I:%M %p")
        except (ValueError, AttributeError):
            return slot_iso

    # ── Main output ─────────────────────────────────────────────────

    def get_available_slots(self) -> Message:
        identifier = (self.contact_identifier or "").strip()
        if not identifier:
            return self._msg("Error: A phone number or email address is required.")

        start_date = self._next_business_day()
        tz_label = self.timezone or "UTC"

        try:
            with httpx.Client(timeout=15.0) as client:
                contact = self._lookup_contact(client)
                target_date, slots = self._find_next_available_day(client, start_date)
        except httpx.HTTPStatusError as exc:
            return self._msg(
                f"GHL API error: {exc.response.status_code} — {exc.response.text[:200]}"
            )
        except httpx.TimeoutException:
            return self._msg("GHL API request timed out. Please try again.")

        date_label = target_date.strftime("%A, %B %d, %Y")

        if contact:
            first = contact.get("firstName", "")
            last = contact.get("lastName", "")
            contact_name = f"{first} {last}".strip() or identifier
        else:
            contact_name = identifier

        if not slots:
            return self._msg(
                f"No available slots found in the next 14 business days for {contact_name}. "
                "The calendar may be fully booked."
            )

        top_two = slots[:2]
        slot_lines = "\n".join(
            f"  {i + 1}. {self._format_time(s)} ({tz_label})"
            for i, s in enumerate(top_two)
        )
        return self._msg(
            f"Hi {contact_name}! Here are available appointment times for {date_label}:\n"
            f"{slot_lines}\n"
            "Would either of these work for you?"
        )

    def _msg(self, text: str) -> Message:
        message = Message(text=text)
        self.status = message
        return message
