# MGR4SMB Assistant — Langflow Multi-Agent Orchestrator

A Langflow-based conversational assistant for small service businesses. A single **Orchestrator Agent** identifies the caller, greets them by name, and routes each turn to a specialized sub-agent that handles general information, sales, customer support, or Jobber account lookups.

---

## Architecture

```
                    ┌──────────────────┐
   User ────────────▶  ORCHESTRATOR    │
                    │     AGENT         │
                    └────────┬──────────┘
                             │  (identify → greet → route)
         ┌───────────────────┼────────────────────┬──────────────────┐
         ▼                   ▼                    ▼                  ▼
  ┌─────────────┐    ┌──────────────┐    ┌──────────────┐   ┌────────────────┐
  │  GREETING   │    │ GENERAL_INFO │    │    SALES     │   │ CUSTOMER_      │
  │   AGENT     │    │    AGENT     │    │    AGENT     │   │ SUPPORT AGENT  │
  └──────┬──────┘    └──────┬───────┘    └──────┬───────┘   └────────┬───────┘
         │                  │                    │                   │
         ▼                  ▼                    ▼                   ▼
   GHL Contact        MongoDB KB          GHL Available        GHL Send OTP
   Lookup             (Atlas Vector)      Slots                GHL Verify OTP
                                          GHL Book             GHL Get Appointments
                                          Appointment          GHL Book / Cancel
                                                               Appointment
```

An optional **Jobber Support Agent** is available alongside the GHL-backed agents for read-only questions about clients, properties, jobs, and visits in Jobber.

---

## Agents

### Orchestrator Agent

The Orchestrator is the entry point for every conversation and the only agent the user talks to directly. Its job is identification and routing — it never answers domain questions itself.

**Responsibilities**
- Collect the user's **email** and **phone** before doing anything else. Both are required; routing is blocked until both are in the conversation history.
- Call the **Greeting Agent** exactly once with the collected email and phone, and surface the returned welcome line to the user.
- Classify the user's intent and call **exactly one** specialist tool per turn.
- Pass full context to the specialist: original question, email, phone, timezone, first name (if known), and an explicit note that the user has already been greeted.
- Return the specialist's response verbatim — no reformatting, no JSON wrapping, no extra summary.

**Routing priority**
1. Existing appointment / booked service → `customer_support_agent`
2. New booking / scheduling request → `sales_agent`
3. General company question → `general_info_agent`
4. Ambiguous → ask one short clarification question

**Guardrails**
- Never skips identification (Step 1).
- Never calls more than one routing tool per turn.
- Never books or modifies appointments without email, phone, and timezone all present.

---

### Greeting Agent

A single-purpose agent whose only job is to produce one welcome line. It exists so that specialist agents can stay focused on their domain and never duplicate greeting logic.

**Behavior**
- Receives the user's email and phone from the Orchestrator.
- Calls **GHL Contact Lookup** (lowercased email preferred; phone as fallback).
- Returns exactly one of:
  - `"Welcome back, {FirstName}!"` — if the contact is found.
  - `"Thanks for contacting us!"` — if no contact is found.

**Guardrails**
- Never asks the user a question.
- Never mentions classification, vendor status, IDs, or any internal details.
- Never adds emojis, follow-ups, or extra sentences.
- Output is always a single line.

---

### General Info Agent

Handles every non-transactional question about the company. Grounded entirely in the knowledge base — it will not speculate.

**Answers questions about**
- Company name, background, and mission
- Services offered and pricing basics
- Business hours and location
- Coverage area
- Policies and FAQs

**Behavior**
- Always consults the **MongoDB Atlas Vector** knowledge base before responding.
- Replies in plain English, concisely.
- If the knowledge base has no answer, says so honestly and offers to help with something else.
- If the user asks to schedule or modify an appointment, explains that another team handles that and defers back to the Orchestrator's routing.

**Guardrails**
- Never invents facts.
- Never schedules, modifies, or cancels appointments.
- Never touches customer account or booking data.
- Skips greetings — the Orchestrator has already welcomed the user.

---

### Sales Agent

Responsible for **new** appointment bookings. Retrieves real availability from GHL and finalizes bookings through the GHL API.

**Three-step flow**
1. **Confirm the service** — If the service is clear, briefly acknowledge it. If missing or ambiguous, ask.
2. **Retrieve availability** — Once service + email/phone + timezone are known, immediately call **GHL Available Slots**. The tool walks forward up to 14 business days to find the next open day, so there's no need to ask the user for a preferred date.
3. **Book** — When the user picks a slot, immediately call **GHL Book Appointment** with the exact ISO slot string, service, and user timezone. Share the confirmation (service, time, confirmation ID).

**Guardrails**
- Emails are lowercased before GHL lookups.
- Never invents slots — availability is always read from the API.
- Never claims a booking succeeded without calling Book Appointment.
- Always passes the user's timezone as `user_timezone` so displayed times match the caller's expectations.
- Routes existing-appointment questions to Customer Support.
- Skips greetings — the Orchestrator has already welcomed the user.

---

### Customer Support Agent

Handles existing appointments: **viewing**, **rescheduling**, and **cancelling**. This is the only agent that reads or modifies customer data, and it enforces strict identity verification before doing so.

**Step 1 — Verify identity (always first)**

*Step 1a — Email + phone match (automatic):*
1. Acknowledge the request and tell the user a verification code will be sent to the email on file.
2. Call **GHL Send OTP** with the user's email and phone.
3. If the response starts with `OTP_FAILED`, tell the user their information doesn't match — without revealing which specific field was wrong — and stop.
4. If the response starts with `OTP_SENT`, continue.

*Step 1b — Emailed code verification:*
1. Ask the user to provide the 6-digit code from their inbox.
2. Call **GHL Verify OTP** with the email and the code.
3. `VERIFIED` → proceed. `UNVERIFIED` → allow up to 2 retries, resend on expiry, end politely after 3 total failed attempts.

**Step 2 — Handle the request**

*Viewing:* Call **GHL Get Appointments**; present service, date/time (in user timezone), and status.

*Rescheduling:* Get Appointments → confirm which one → **Cancel Appointment** on the old one → **Available Slots** → present options → **Book Appointment** on the new slot → confirm with old-vs-new details.

*Cancelling:* Get Appointments → confirm which one → read back for explicit user approval → **Cancel Appointment** → confirm.

**Guardrails**
- Never accesses appointment data before verification succeeds.
- Never reveals which specific field (email or phone) didn't match — always a generic "information does not match" message.
- Always passes the user's timezone to every tool call.
- When rescheduling, always cancels the old appointment **before** booking the new one.
- Routes new-booking requests to the Sales team.
- Never confirms changes without the user's explicit approval.
- Skips greetings — the Orchestrator has already welcomed the user.

---

### Jobber Support Agent *(optional)*

A read-only agent for answering questions about a Jobber account. It cannot book, modify, or cancel anything — any change must be made in Jobber directly.

**Answers questions about**
- **Clients** — contact info, matching records
- **Properties** — service addresses per client
- **Jobs** — titles, status, dates, totals, the property each job is on
- **Visits** — scheduled and completed appointments per client/job

**Two-step flow**
1. **Resolve the client first** — Every downstream Jobber tool requires the Jobber client ID (base64). The agent always calls **Jobber Get Clients** first with whatever identifier the user provided (name / email / phone / ID). If there's one match, it uses that ID. Multiple matches → asks the user to disambiguate. Zero matches → reports it honestly and asks for a different identifier.
2. **Answer with the right tool** — Based on intent, calls **Get Properties**, **Get Jobs**, or **Get Visits** with the resolved client ID. For compound questions (e.g., "list every visit at each property"), it chains calls and cross-references results through the job → property relationship.

**Guardrails**
- Never guesses a Jobber client ID — it must come from a Get Clients response.
- Never picks one among multiple matches — always asks the user.
- Reuses the resolved client ID across follow-ups in the same conversation.
- Read-only: will not claim to book, reschedule, cancel, update, or delete anything in Jobber.
- Summarizes — presents clean bullet lists rather than raw field dumps.

---

All six agents share the same prompt skeleton — **Role → Context → Personalization → Your Tools → Steps → Important Rules → Tone and Format** — so behavior stays predictable and edits propagate consistently across the flow.

---

## Conversation Flow

1. **Identify** — Orchestrator asks for email + phone (required before any routing).
2. **Greet** — Orchestrator calls the Greeting Agent, which runs GHL Contact Lookup and returns a personalized welcome.
3. **Route** — Orchestrator picks exactly one specialist based on intent:
   - Existing booking → Customer Support
   - New booking → Sales
   - General question → General Info
4. **Context passthrough** — The specialist receives the email, phone, timezone, first name, and a note that the user has already been greeted (so it doesn't greet again).
5. **Respond** — The specialist's reply is returned verbatim to the user.

---

## Custom Components

Located under [artifact/components/](artifact/components/):

### GoHighLevel ([artifact/components/GoHighLevel/](artifact/components/GoHighLevel/))
- `contact_lookup.py` — POST /contacts/search by email or phone; returns name, id, and classification.
- `available_slots.py` — GET /calendars/{id}/free-slots; walks forward up to 14 business days to find the next open day.
- `book_appointment.py` — POST /calendars/events/appointments.
- `get_appointments.py` — GET /contacts/{id}/appointments.
- `cancel_appointment.py` — DELETE /calendars/events/{id}.
- `send_otp.py` / `verify_otp.py` — Identity verification via emailed 6-digit code.

### Jobber ([artifact/components/Jobber/](artifact/components/Jobber/))
- `getClients.py` — GraphQL client search by name / email / phone / Jobber ID. Auto-refreshes OAuth tokens.
- `getProperties.py` — Service addresses for a client.
- `getJobs.py` — Jobs (with nested property) for a client.
- `getVisits.py` — Scheduled and completed visits for a client.

All Jobber components share the same OAuth pattern: tokens are read from `.tokens.json` in the project root, refreshed automatically on 401, and the rotated refresh token is written back to disk.

All GHL components follow the same pattern: `httpx.Client` calls wrapped in `try/except` for `HTTPStatusError` / `TimeoutException`, with a `_msg()` helper that sets `self.status` and returns a `Message`.

---

## Running Locally

```bash
./langflow-dev.sh
```

The script:
- Activates the local `.venv`
- Kills any running Langflow processes
- Exports `LANGFLOW_COMPONENTS_PATH=artifact/components` so custom components load at startup
- Exports `GRPC_DNS_RESOLVER=native` (required on macOS to reach Gemini)
- Exports `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` (macOS fork-safety workaround for Langflow 1.8.4+ gunicorn workers)
- Starts Langflow on port **7860**

Open http://127.0.0.1:7860 and import the latest flow file (e.g. `mgr4smb - Orquestrator V7.json`).

### Required Secrets (configured in Langflow Settings → Variables)
- `GHL_API_KEY` — GoHighLevel Private Integration Token
- `GHL_LOCATION_ID` — GHL Sub-Account ID
- `GHL_CALENDAR_ID` — Calendar used for availability and bookings
- `GOOGLE_API_KEY` — Gemini model for all agents
- `MONGODB_ATLAS_URI` — Knowledge base connection for the General Info Agent
- `JOBBER_CLIENT_ID` / `JOBBER_CLIENT_SECRET` *(if using the Jobber agent)*

---

## Flow Versions

| File | Notes |
|---|---|
| `mgr4smb - Orquestrator V6.json` | Previous stable flow. |
| `mgr4smb - Orquestrator V7.json` | Current. Adds the dedicated Greeting Agent and standardized prompt templates across all specialists. |

---

## Design Principles

- **One routing call per turn.** The Orchestrator never calls more than one specialist per user message.
- **Greet once.** Only the Greeting Agent greets; specialists are instructed to skip greetings.
- **Verification before mutation.** Customer Support never reads or changes appointment data until OTP verification succeeds.
- **Read-only for Jobber.** The Jobber agent can only retrieve data; any change must happen in Jobber directly.
- **Never invent data.** Agents must call their tools and only answer from what was returned.
- **Plain English output.** Specialists never return JSON to the user.
- **Consistent prompts.** Every specialist uses the same section skeleton so behavior is predictable and edits are easy.
