# Plan: Hybrid Authentication for MGR4SMB MVP

## Context
The MGR4SMB Langflow flow is invoked by a GoHighLevel AI agent. Currently there is no authentication — any caller can reach the Orchestrator. Two auth layers are needed:

- **Layer 1 (Global):** Prove the calling GHL instance is trusted
- **Layer 2 (Individual):** Confirm identity of contacts and vendors via CRM lookup + PIN challenge

**Design constraint:** Avoid creating new custom components where possible. Use built-in Langflow components or extend/reuse existing ones.

The GHL→Langflow integration has not yet been configured, so the payload format must also be defined.

---

## Credential Storage — MongoDB

All GHL credentials live in a MongoDB collection (`mgr4smb.ghl_config`) for centralized management.

**Document (`ghl_config` collection):**
```json
{
  "_id": "ghl_credentials",
  "location_id": "<ghl_location_id>",
  "api_key": "<ghl_private_integration_token>",
  "classification_field_id": "<ghl_custom_field_id_for_contact_type>",
  "pin_field_id": "<ghl_custom_field_id_for_security_pin>"
}
```

---

## Payload Contract (GHL AI → Langflow API)

GHL AI must POST to Langflow's `/api/v1/run/{flow_id}` with:

**Header:** `Authorization: Bearer <langflow_api_key>` ← handles global auth at platform level

**Body:**
```json
{
  "input_value": "{\"message\": \"caller's natural language message\", \"caller_phone\": \"+19522281752\", \"location_id\": \"<ghl_location_id>\"}",
  "input_type": "chat",
  "output_type": "chat",
  "session_id": "<caller_phone>"
}
```

The `location_id` inside the JSON body provides a second validation factor (see Layer 1 below).

---

## Architecture

```
GHL AI → Langflow API (Authorization: Bearer <langflow_api_key>)
              │  [Platform-level auth — no component needed]
         ChatInput
              │
         RegexRouter (existing, reused)
         checks payload contains expected location_id
              │ PASS                │ FAIL
              │              ChatOutput-Unauthorized
         FrontDesk Orchestrator
         (tools: ghl_contact_lookup, company_knowledge_search)
              │
              ├── InfoAgent
              ├── LeadAgent
              ├── EmergencyAgent
              ├──► AuthAgent (built-in Agent node, new prompt)
              │    (tools: ghl_contact_lookup w/ PIN mode)
              │         │ VERIFIED          │ FAILED
              │         ├──► CustomerAgent  └──► ChatOutput-AuthFailed
              │         └──► VendorAgent
              └── EmergencyAgent
```

---

## Layer 1: Global Authentication — No New Component ✅ IMPLEMENTED

**Two sub-layers, zero new components:**

### Sub-layer A: Langflow Platform API Key Auth
**Status:** Configuration step (no code)

**Steps to enable:**
1. In Langflow UI → Settings → General → turn on **"Auto Login"** OFF and enable **API Key Auth**
2. Go to Settings → API Keys → create a new key (label it `ghl-mgr4smb`)
3. Copy the key — this is the `<langflow_api_key>` GHL AI must include in every request

**GHL AI request header:**
```
Authorization: Bearer <langflow_api_key>
```
Requests without a valid key return HTTP 403 before reaching the flow.

---

### Sub-layer B: Location ID Verification (flow-level)
**Status:** `RegexRouter` extended ✅ — flow wiring needed in Langflow UI

`artifact/components/MIscellaneus/regex_router.py` now has two routing outputs:
- **Match** (`true_response`) — fires when pattern is found; wire to FrontDesk Orchestrator
- **No Match** (`false_response`) — fires when pattern is missing; wire to ChatOutput-Unauthorized
- **Result** (`output`) — legacy "true"/"false" string output, unchanged

**Steps to wire in Langflow UI (after restarting Langflow to pick up component changes):**
1. Drag a **Regex Router** node onto the canvas
2. Set **Operator** = `contains`, **Pattern** = your GHL `location_id` value
3. Delete the existing edge from `ChatInput → FrontDesk Orchestrator`
4. Wire: `ChatInput (Message) → RegexRouter (Text)`
5. Wire: `RegexRouter (Match) → FrontDesk Orchestrator (Input)`
6. Add a new **Chat Output** node, label it `Unauthorized`
7. Wire: `RegexRouter (No Match) → Chat Output Unauthorized`

---

## Layer 2: Contact/Vendor Authentication — Extend Existing Component

Rather than creating a new `GHLPinVerify` component, **extend the existing `GoHighLevelContactLookup`** at `artifact/components/GoHighLevel/contact_lookup.py`.

### Changes to `contact_lookup.py`
Add two optional inputs:
- `pin_to_verify` (MessageTextInput, optional, tool_mode=True) — the PIN entered by the caller; if provided, enables PIN verification mode
- `pin_field_id` (StrInput, optional) — GHL custom field ID storing the contact's PIN

**Updated logic in `build_output()`:**
- If `pin_to_verify` is provided:
  - After finding the contact, also extract the PIN custom field value
  - Compare `pin_to_verify.strip()` == stored PIN
  - Return `"VERIFIED: <contact_name> (ID: <id>)"` or `"PIN_MISMATCH"` or `"CONTACT_NOT_FOUND"`
- If `pin_to_verify` is not provided: behaves exactly as before (backward compatible)

**PIN verification is deterministic (no LLM sees the PIN value) — the comparison happens in Python.**

---

## Flow JSON Changes (MGR4SMB MVP (2).json)

### 1. Add `RegexRouter` node (location_id gate)
- Wire: `ChatInput.message → RegexRouter.text`
- Configure: operator=`contains`, pattern=`<ghl_location_id>`
- Wire: `RegexRouter output "true" → FrontDesk Orchestrator.input_value`
- Add `ChatOutput-Unauthorized` wired to RegexRouter output `"false"`

### 2. Add `GoHighLevelContactLookup` (standard mode) as a tool for FrontDesk Orchestrator
- Already exists; add it as a node with `api_key`, `location_id`, `classification_field_id` configured
- Wire its Toolset output to the Orchestrator's tools input
- Orchestrator calls this to confirm contact vs. vendor vs. prospect before routing

### 3. Add dedicated `AuthAgent` node (built-in Langflow Agent)
- A standard Langflow Agent node — no custom component
- Tools: one instance of `GoHighLevelContactLookup` with `pin_field_id` configured (PIN mode)
- New Prompt Template node with AuthAgent instructions (see below)
- Receives routing JSON from FrontDesk Orchestrator (contact_id + classification)
- On `VERIFIED` → passes caller info to CustomerAgent or VendorAgent
- On `PIN_MISMATCH` after retry → routes to `ChatOutput-AuthFailed`

**AuthAgent Prompt Template (new node):**
```
You are AuthAgent, the identity verification step for MGR4SMB.

You receive a routing JSON with ghl_contact_id and ghl_classification.

Steps:
1. Greet the caller by name if available.
2. Ask: "For your security, please provide your 4-digit account PIN."
3. Call ghl_contact_lookup with contact_id=<ghl_contact_id> and pin_to_verify=<entered_pin>.
4. If result is "VERIFIED": output a routing JSON:
   {"agent": "<CustomerAgent or VendorAgent>", "ghl_contact_id": "...", "ghl_classification": "...", "authenticated": true}
5. If "PIN_MISMATCH": offer one retry.
6. On second failure: say "I'm unable to verify your identity. Let me connect you with a team member."
   Then stop — do not route further.

Only output the routing JSON on success. Never output JSON on failure.
```

### 4. Update FrontDesk Orchestrator system prompt (Prompt Template-fF2Dg)
Add after contact-collection step:
```
PAYLOAD PARSING: The input is a JSON string. Extract "message" as the user's request
and "caller_phone" for identity lookup.

CONTACT VERIFICATION (before routing to CustomerAgent or VendorAgent):
- Call ghl_contact_lookup with caller_phone (search_type: phone)
- Add to routing JSON:
  - "ghl_contact_id": returned contact ID, or null
  - "ghl_classification": "contact", "vendor", "prospect", or "unknown"
- If classification is "prospect" or contact not found → route to LeadAgent
```

Add `ghl_contact_id` and `ghl_classification` to the routing JSON schema.

### 5. Update CustomerAgent prompt (Prompt Template-nVWqU)
Remove any identity-collection steps — the caller arrives already authenticated via AuthAgent.
Add: `"The caller has been identity-verified. ghl_contact_id is available in the routing data. Proceed directly with service."`

### 6. Update VendorAgent prompt (Prompt Template-OJHjD)
Same — remove identity collection, add verified-caller note.

### 7. Update Orchestrator routing: contact/vendor → AuthAgent (not directly to specialist)
When `ghl_classification` is "contact" or "vendor", set `"agent": "AuthAgent"` in routing JSON.
AuthAgent then routes onward to CustomerAgent or VendorAgent after PIN success.

### 8. Update `__init__.py`
No new imports needed (only modifying existing `GoHighLevelContactLookup`).

---

## Critical Files

| File | Change |
|------|--------|
| `artifact/components/GoHighLevel/contact_lookup.py` | **Modify** — add optional `pin_to_verify` + `pin_field_id` inputs and PIN comparison logic |
| `MGR4SMB MVP (2).json` | Add RegexRouter gate, add AuthAgent node + prompt, add ContactLookup tool nodes, update Orchestrator/CustomerAgent/VendorAgent prompts, rewire edges |
| Langflow Settings | Enable API Key Auth (one-time platform config, no file change) |

**Reused components (no changes):**
- `artifact/components/MIscellaneus/regex_router.py` — location_id gate (Layer 1)
- `artifact/components/GoHighLevel/contact_lookup.py` — reused as two tool instances (standard lookup for Orchestrator; PIN mode for AuthAgent)

---

## Verification

1. **No auth header:** Request without `Authorization` header → rejected by Langflow platform (HTTP 401)
2. **Wrong location_id:** Valid API key but wrong location_id in payload → `UNAUTHORIZED` from RegexRouter
3. **Auth pass:** Valid key + valid location_id → FrontDesk Orchestrator greeting
4. **Unknown phone:** Valid auth, phone not in GHL → routes to LeadAgent
5. **Contact + correct PIN:** CRM match → AuthAgent → PIN challenge → `VERIFIED` → CustomerAgent
6. **Contact + wrong PIN:** `PIN_MISMATCH` → retry → second fail → escalation
7. **Vendor path:** Same as contact path via VendorAgent
