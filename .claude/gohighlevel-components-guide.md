# GoHighLevel Langflow Components Guide

## API Fundamentals

**Base URL:** `https://services.leadconnectorhq.com`

**Required Headers (all requests):**
```
Authorization: Bearer {api_key}
Content-Type: application/json
Version: 2021-07-28
```

**Auth:** Use a Private Integration Token (Settings > Integrations > Private Integrations in GHL) or an OAuth 2.0 Bearer token for marketplace apps.

**Rate Limits:**
- Burst: 100 requests / 10 seconds per resource
- Daily: 200,000 requests / day per resource

---

## Contacts

### Search Contacts (current)
```
POST /contacts/search
```
Body:
```json
{
  "locationId": "abc123",
  "pageLimit": 25,
  "filters": [
    {
      "group": "AND",
      "filters": [
        { "field": "email", "operator": "eq", "value": "user@example.com" }
      ]
    }
  ]
}
```
Filter fields: `email`, `phone`, `firstName`, `lastName`, `tags`, `dateAdded`
Operators: `eq`, `ne`, `contains`, `startsWith`, `endsWith`, `gt`, `lt`

### Get Contact by ID
```
GET /contacts/{contactId}
```

### Create / Update Contact
```
POST /contacts/
PUT  /contacts/{contactId}
```

### customFields in responses
```json
"customFields": [
  { "id": "field_id_here", "value": "client", "fieldValueType": "TEXT" }
]
```
> **Important:** Use `id` (not `key`) to match custom fields in code.
> Find field IDs under: GHL > Settings > Custom Fields > click field > copy ID from URL or field details.

---

## Custom Fields

### List all custom fields for a location
```
GET /locations/{locationId}/customFields
```
Response includes each field's `id`, `name`, `fieldKey`, `dataType`.

### Get a specific field
```
GET /locations/{locationId}/customFields/{id}
```

---

## Conversations / Messaging

### Send SMS or Email
```
POST /conversations/messages
```
Body:
```json
{
  "type": "SMS",
  "contactId": "abc",
  "locationId": "xyz",
  "message": "Hello from Langflow"
}
```
Types: `SMS`, `Email`, `WhatsApp`, `GMB`, `FB`

### Get conversations for a contact
```
GET /conversations/search?locationId={id}&contactId={contactId}
```

---

## Opportunities (Pipeline / Deals)

### Search opportunities
```
GET /opportunities/search?location_id={id}&pipeline_id={pid}
```

### Create opportunity
```
POST /opportunities
```

---

## Calendars & Appointments

### List calendars for a location
```
GET /calendars/?locationId={id}
```
Response: `{ "calendars": [ { "id", "name", "description", "locationId", "isActive" } ] }`

### Get appointments for a contact
```
GET /contacts/{contactId}/appointments
```
Response: `{ "events": [ { ...appointment object... } ] }`

### Get calendar events (date range)
```
GET /calendars/events?locationId={id}&calendarId={id}&startTime={epoch_ms}&endTime={epoch_ms}
```
Query params:
- `locationId` (required)
- `calendarId` (optional, filter by calendar)
- `startTime` — Unix timestamp in **milliseconds**
- `endTime` — Unix timestamp in **milliseconds**

### Get appointment by ID
```
GET /calendars/appointments/{appointmentId}
```

### Create appointment
```
POST /calendars/events/appointments
```
Body:
```json
{
  "calendarId": "abc",
  "locationId": "xyz",
  "contactId": "def",
  "startTime": "2024-03-01T10:00:00Z",
  "endTime": "2024-03-01T11:00:00Z",
  "title": "Consultation",
  "appointmentStatus": "confirmed",
  "assignedUserId": "user123",
  "address": "123 Main St",
  "ignoreDateRange": false,
  "toNotify": true
}
```

### Update appointment
```
PUT /calendars/events/appointments/{appointmentId}
```

### Get calendar free slots (availability)
```
GET /calendars/{calendarId}/free-slots?startDate={epoch_ms}&endDate={epoch_ms}&timezone={tz}
```
Response: `{ "slots": { "2024-03-01": [ { "slots": ["09:00", "10:00"] } ] } }`

### Get availability schedule for a calendar
```
GET /calendars/schedules/event-calendar/{calendarId}
```

### Appointment object schema
```json
{
  "id": "string",
  "title": "string",
  "calendarId": "string",
  "contactId": "string",
  "groupId": "string",
  "appointmentStatus": "confirmed | cancelled | showed | noshow | invalid",
  "assignedUserId": "string",
  "users": ["userId"],
  "notes": "string",
  "source": "string",
  "address": "string",
  "startTime": "2024-03-01T10:00:00Z",
  "endTime": "2024-03-01T11:00:00Z",
  "dateAdded": "2024-02-28T12:00:00Z",
  "dateUpdated": "2024-02-28T12:00:00Z"
}
```

> **Time format:** GHL accepts ISO 8601 strings for create/update (`2024-03-01T10:00:00Z`),
> but query params for event listing use **Unix milliseconds** (e.g. `1709280000000`).

---

## Workflows

### Add contact to workflow
```
POST /contacts/{contactId}/workflow/{workflowId}
```

---

## Component Template Pattern

All GHL components should follow this structure:

```python
from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message
import httpx

GHL_API_BASE = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"

class MyGHLComponent(Component):
    display_name = "GHL ..."
    description = "..."
    icon = "..."
    name = "MyGHLComponent"

    inputs = [
        MessageTextInput(name="input_value", display_name="Input", tool_mode=True),
        SecretStrInput(name="api_key", display_name="GHL API Key"),
        StrInput(name="location_id", display_name="Location ID"),
    ]

    outputs = [
        Output(display_name="Result", name="output", method="build_output"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Version": GHL_API_VERSION,
        }

    def build_output(self) -> Message:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                f"{GHL_API_BASE}/...",
                headers=self._headers(),
                params={"locationId": self.location_id},
            )
            response.raise_for_status()
            data = response.json()
        message = Message(text=str(data))
        self.status = message
        return message
```

---

## Planned Components

| Component | Endpoint | Purpose |
|-----------|----------|---------|
| Contact Lookup | `POST /contacts/search` | Find contact by email/phone, return classification |
| Send SMS | `POST /conversations/messages` | Send SMS to a contact |
| Create Contact | `POST /contacts/` | Add new contact to GHL |
| Update Contact | `PUT /contacts/{id}` | Update contact fields |
| Calendar Lookup | `GET /contacts/{id}/appointments` + `GET /calendars/events` | Fetch upcoming appointments for a contact |
| Add to Workflow | `POST /contacts/{id}/workflow/{wfId}` | Trigger a GHL workflow |
| Create Opportunity | `POST /opportunities` | Add deal to pipeline |
| List Custom Fields | `GET /locations/{id}/customFields` | Discover available custom fields |

---

## Tips

- **Finding field IDs:** GHL UI > Settings > Custom Fields > click field > the ID is in the URL or field panel
- **Finding Location ID:** GHL URL when viewing contacts: `app.gohighlevel.com/location/{locationId}/contacts`
- **Phone format:** GHL expects E.164 format: `+12345678900`
- **Deprecated:** `GET /contacts/` is deprecated — use `POST /contacts/search` instead
