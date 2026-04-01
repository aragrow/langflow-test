#!/usr/bin/env python3
"""
Standalone test of the GHL Available Slots logic — runs outside Langflow.
Uses the same API calls the component makes, step by step.
"""

import httpx
from datetime import datetime, timezone, timedelta

# ── Config (matches Langflow global variables) ──────────────────────────
API_KEY = "pit-ff639a42-f75f-4056-8806-4eb82e609aa2"
LOCATION_ID = "6PSiia8Q984m32jw8wJE"
CALENDAR_ID = "ax0wqrItV0HadykGApYE"
TIMEZONE = "America/New_York"
CONTACT_IDENTIFIER = "davidarago99@gmail.com"

GHL_API_BASE = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "Version": GHL_API_VERSION,
}


# ── 1. Contact lookup ───────────────────────────────────────────────────
def lookup_contact(client: httpx.Client, identifier: str) -> dict | None:
    search_field = "email" if "@" in identifier else "phone"
    body = {
        "locationId": LOCATION_ID,
        "pageLimit": 1,
        "filters": [
            {
                "group": "AND",
                "filters": [
                    {"field": search_field, "operator": "eq", "value": identifier}
                ],
            }
        ],
    }
    resp = client.post(f"{GHL_API_BASE}/contacts/search", headers=HEADERS, json=body)
    resp.raise_for_status()
    contacts = resp.json().get("contacts", [])
    if contacts:
        c = contacts[0]
        print(f"  Contact found: {c.get('firstName','')} {c.get('lastName','')} (id={c.get('id','')})")
        return c
    print("  No contact found for", identifier)
    return None


# ── 2. Free-slots for a single day ──────────────────────────────────────
def get_free_slots(client: httpx.Client, target_date: datetime) -> list[str]:
    start_ms = int(target_date.timestamp() * 1000)
    end_of_day = target_date.replace(hour=23, minute=59, second=59)
    end_ms = int(end_of_day.timestamp() * 1000)

    # IMPORTANT: do NOT send locationId — GHL returns 422 if you do
    params = {
        "startDate": start_ms,
        "endDate": end_ms,
    }
    if TIMEZONE:
        params["timezone"] = TIMEZONE

    url = f"{GHL_API_BASE}/calendars/{CALENDAR_ID}/free-slots"
    print(f"  GET {url}")
    print(f"      params={params}")

    resp = client.get(url, headers=HEADERS, params=params)
    print(f"      status={resp.status_code}")
    resp.raise_for_status()

    data = resp.json()
    date_key = target_date.strftime("%Y-%m-%d")
    day_data = data.get(date_key, {})
    slots = day_data.get("slots", [])
    print(f"      response keys={list(data.keys())}")
    print(f"      slots for {date_key}: {slots[:3]}{'...' if len(slots) > 3 else ''}")
    return slots


# ── 3. Walk forward to find the next day with availability ──────────────
def find_next_available_day(
    client: httpx.Client, start: datetime, max_days: int = 14
) -> tuple[datetime, list[str]]:
    candidate = start
    for day_num in range(max_days):
        print(f"\n  Day {day_num + 1}: {candidate.strftime('%Y-%m-%d (%A)')}")
        slots = get_free_slots(client, candidate)
        if slots:
            return candidate, slots
        candidate += timedelta(days=1)
        while candidate.weekday() >= 5:  # skip weekends
            candidate += timedelta(days=1)
    return start, []


# ── 4. Format slots nicely ──────────────────────────────────────────────
def format_slot(slot: str) -> str:
    try:
        dt = datetime.fromisoformat(slot.replace("Z", "+00:00"))
        return dt.strftime("%I:%M %p")
    except (ValueError, AttributeError):
        return slot


# ── Main ────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("GHL Available Slots — Standalone Test")
    print("=" * 60)

    # Next business day
    today = datetime.now(timezone.utc)
    start_date = today + timedelta(days=1)
    while start_date.weekday() >= 5:
        start_date += timedelta(days=1)
    start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

    print(f"\nToday (UTC): {today.strftime('%Y-%m-%d %H:%M')}")
    print(f"Starting search from: {start_date.strftime('%Y-%m-%d (%A)')}")

    with httpx.Client(timeout=15.0) as client:
        # Step 1: Contact
        print("\n── Step 1: Contact Lookup ──")
        contact = lookup_contact(client, CONTACT_IDENTIFIER)

        # Step 2: Find slots
        print("\n── Step 2: Find Available Slots ──")
        target_date, slots = find_next_available_day(client, start_date)

    # Step 3: Format output
    print("\n── Step 3: Output ──")
    date_label = target_date.strftime("%A, %B %d, %Y")

    if contact:
        first = contact.get("firstName", "")
        last = contact.get("lastName", "")
        contact_name = f"{first} {last}".strip() or CONTACT_IDENTIFIER
    else:
        contact_name = CONTACT_IDENTIFIER

    if not slots:
        print(f"No available slots found for {contact_name}.")
        print("The calendar may be fully booked for the next 14 business days.")
    else:
        top_two = slots[:2]
        print(f"\nHi {contact_name}! Here are two available appointment times for {date_label}:")
        for i, s in enumerate(top_two):
            print(f"  {i + 1}. {format_slot(s)} ({TIMEZONE})")
        print("Would either of these work for you?")

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
