# =============================================================================
# Jobber – Get Clients Component
# =============================================================================
# Purpose : Searches and returns Jobber clients via the GraphQL API.
# Auth    : OAuth 2.0 – tokens are read from a .tokens.json file keyed by
#           client_id. The file is updated automatically when a token refresh
#           occurs (Jobber access tokens expire after 60 minutes).
# Search  : Accepts an optional search_value. The input format is auto-detected:
#             @           → filter by email (substring match)
#             +/digits    → filter by phone (digit-only comparison)
#             base64-like → fetch directly by Jobber ID (fastest path)
#             anything    → filter by first/last name or company name
# Output  : Human-readable client list, or a "no clients found" message.
#           Also exposes a Toolset output so it can be wired to an agent.
# Template: Copy this file as the starting point for other Jobber components.
#           Follow the same auth pattern, query constants, and build_output flow.
# =============================================================================

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, SecretStrInput
from lfx.schema.message import Message

import httpx
import json
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# API constants
# ---------------------------------------------------------------------------
JOBBER_GRAPHQL_URL = "https://api.getjobber.com/api/graphql"    # All GraphQL requests go here (POST)
JOBBER_TOKEN_URL   = "https://api.getjobber.com/api/oauth/token" # Token refresh endpoint
JOBBER_VERSION     = "2023-11-15"                                # Required X-JOBBER-GRAPHQL-VERSION header

# ---------------------------------------------------------------------------
# In-process token cache
# ---------------------------------------------------------------------------
# Holds the access_token for the lifetime of the Langflow server process so a
# refreshed token is reused on the next call without a round-trip to Jobber.
# On cache miss, _active_token() reads from .tokens.json and re-warms this var.
_TOKEN_CACHE: dict[str, str] = {}   # keyed by "access_token" (single entry)

# ---------------------------------------------------------------------------
# GraphQL queries
# ---------------------------------------------------------------------------

# Used when search_value is detected as a Jobber ID (base64 string).
# Fetches a single client directly — fastest path, no iteration needed.
_QUERY_BY_ID = """
query GetClientById($id: ID!) {
  client(id: $id) {
    id
    firstName
    lastName
    companyName
    emails { address description primary }
    phones { number description primary }
  }
}
"""

# Used for email, phone, and name searches (and when no filter is provided).
# Returns up to 50 clients per page. pageInfo supports cursor-based pagination
# if you need to extend this component to fetch beyond 50 results.
_QUERY_ALL = """
query GetClients($cursor: String) {
  clients(after: $cursor, first: 50) {
    nodes {
      id
      firstName
      lastName
      companyName
      emails { address description primary }
      phones { number description primary }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

# ---------------------------------------------------------------------------
# Helpers (module-level so they can be reused by other components)
# ---------------------------------------------------------------------------

def _detect_search_type(value: str) -> str:
    """
    Infer the intended filter type from the format of search_value.

    Rules (evaluated in order):
      1. Contains '@'                         → "email"
      2. Matches phone pattern (+/digits/etc) → "phone"
      3. Looks like a base64 Jobber ID        → "id"   (12+ alphanum chars, no spaces)
      4. Anything else                        → "name"

    Jobber IDs are base64-encoded global IDs, e.g. "Q2xpZW50OjEyMzQ="
    Phone matching strips all non-digits before comparing, so formats like
    "(555) 123-4567", "+1-555-123-4567", and "5551234567" all resolve to "phone".
    """
    value = value.strip()
    if "@" in value:
        return "email"
    if re.match(r"^\+?[\d\s\-().]{7,15}$", value):
        return "phone"
    # Jobber IDs are base64-encoded strings, typically 12+ chars with no spaces
    if re.match(r"^[A-Za-z0-9+/=]{12,}$", value) and " " not in value:
        return "id"
    return "name"


# ---------------------------------------------------------------------------
# Component
# ---------------------------------------------------------------------------

class JobberGetClients(Component):
    """
    Langflow component that fetches Jobber clients via the GraphQL API.

    Authentication flow:
      1. Tokens (access_token + refresh_token) are read from a .tokens.json file
         keyed by client_id. The file path is configured via the Token File Path input.
      2. On each request the access_token from the file (or in-process cache) is used.
      3. If the request fails with an auth error, the refresh_token from the file is
         exchanged for a new access_token. Both the new access_token and rotated
         refresh_token are written back to the file immediately.
      (Jobber access tokens expire after 60 minutes; refresh tokens rotate on use.)

    To use this as a template for another Jobber component:
      1. Copy this file and rename the class.
      2. Replace _QUERY_BY_ID / _QUERY_ALL with your target queries.
      3. Update _execute_by_id / _execute_all to unpack the new response shape.
      4. Update _filter_* and build_output formatting as needed.
      5. Keep _headers, _refresh_access_token, and _run_with_token_refresh unchanged.
    """

    display_name = "Jobber Get Clients"
    description  = (
        "Fetches clients from Jobber using OAuth 2.0 authentication. "
        "Tokens are loaded from a .tokens.json file and refreshed automatically when expired. "
        "Supports optional search by name, email, phone, or Jobber ID."
    )
    documentation: str = "https://developer.getjobber.com/docs/"
    icon = "users"
    name = "JobberGetClients"

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------
    inputs = [
        # Primary tool input — the agent passes the search value here.
        # tool_mode=True makes this the argument the orchestrator agent
        # fills in when calling this component as a tool.
        MessageTextInput(
            name="search_value",
            display_name="Search (optional)",
            info=(
                "Auto-detected filter. Leave blank to return all clients (up to 50). "
                "Examples: 'john@example.com' (email), '+15551234567' (phone), "
                "'Q2xpZW50OjEy' (Jobber ID), 'John Smith' (name)."
            ),
            tool_mode=True,
            value="",
        ),
        # Required only for token refresh. Not used in GraphQL requests directly.
        SecretStrInput(
            name="client_id",
            display_name="Client ID",
            info="Jobber app Client ID (from Developer Center). Required for token refresh.",
            load_from_db=True,
        ),
        SecretStrInput(
            name="client_secret",
            display_name="Client Secret",
            info="Jobber app Client Secret (from Developer Center). Required for token refresh.",
            load_from_db=True,
        ),
    ]

    # ------------------------------------------------------------------
    # Outputs
    # ------------------------------------------------------------------
    outputs = [
        # Primary output — returns a human-readable Message with client list.
        Output(display_name="Response", name="output", method="build_output"),
        # Toolset output — connect to an Agent's Tools port to expose this
        # component as a callable tool. Uses the inherited to_toolkit() method.
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _headers(self, token: str) -> dict:
        """
        Build the required headers for every Jobber GraphQL request.
        X-JOBBER-GRAPHQL-VERSION pins the API schema version so breaking
        changes in future Jobber releases don't affect this component.
        """
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-JOBBER-GRAPHQL-VERSION": JOBBER_VERSION,
        }

    def _refresh_access_token(self, client: httpx.Client) -> str:
        """
        Exchange the refresh_token (read from .tokens.json) for a new access_token.
        Both the new access_token and the rotated refresh_token are written back
        to the file immediately — Jobber invalidates the old refresh_token on use.
        Also updates _TOKEN_CACHE so subsequent calls this session skip the file read.
        Raises RuntimeError if no refresh_token is available in the file.
        Raises httpx.HTTPStatusError if the Jobber token endpoint returns an error.
        """
        file_entry    = self._load_token_file()
        refresh_token = file_entry.get("refresh_token")
        if not refresh_token:
            raise RuntimeError(
                "No refresh_token found in token file. "
                "Re-authorise the Jobber app and write both tokens to the file."
            )

        response = client.post(
            JOBBER_TOKEN_URL,
            json={
                "client_id":     self.client_id,
                "client_secret": self.client_secret,
                "grant_type":    "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        payload     = response.json()
        new_token   = payload["access_token"]
        new_refresh = payload.get("refresh_token", refresh_token)

        # 1. Update in-process cache for fast reuse within this server session.
        _TOKEN_CACHE["access_token"] = new_token

        # 2. Persist both tokens so they survive a server restart.
        self._save_token_file(new_token, new_refresh)

        return new_token

    def _execute_by_id(self, client: httpx.Client, token: str) -> list:
        """
        Fetch a single client by their Jobber ID using _QUERY_BY_ID.
        Returns a one-element list so the caller always works with a list,
        or an empty list if the ID was not found or an auth error occurred.
        Auth errors are flagged on the returned list via _auth_error so
        _run_with_token_refresh can detect and retry them.
        """
        response = self._post(
            client, token,
            {"query": _QUERY_BY_ID, "variables": {"id": self.search_value.strip()}},
        )
        if self._is_auth_error(response):
            result = []
            result._auth_error = True  # type: ignore[attr-defined]
            return result
        response.raise_for_status()
        contact = response.json().get("data", {}).get("client")
        return [contact] if contact else []

    def _execute_all(self, client: httpx.Client, token: str) -> list:
        """
        Fetch the first page of clients (up to 50) using _QUERY_ALL.
        Cursor pagination is available via pageInfo.endCursor if you need
        to extend this to fetch all pages.
        Auth errors are flagged on the returned list for retry handling.
        """
        response = self._post(
            client, token,
            {"query": _QUERY_ALL, "variables": {"cursor": None}},
        )
        if self._is_auth_error(response):
            result = []
            result._auth_error = True  # type: ignore[attr-defined]
            return result
        response.raise_for_status()
        return response.json().get("data", {}).get("clients", {}).get("nodes", [])

    def _filter_clients(self, clients: list, search_type: str) -> list:
        """
        Filter a list of client dicts by search_value using the detected search_type.

        - email : substring match against all email addresses (case-insensitive)
        - phone : digit-only comparison so formatting differences don't matter
        - name  : substring match against full name and company name
        """
        search = self.search_value.strip().lower()
        if not search:
            return clients

        results = []
        for c in clients:
            if search_type == "email":
                match = any(search in e["address"].lower() for e in c.get("emails", []))
            elif search_type == "phone":
                # Strip non-digits from both sides so "+1 (555) 123-4567" == "5551234567"
                digits = re.sub(r"\D", "", search)
                match = any(digits in re.sub(r"\D", "", p["number"]) for p in c.get("phones", []))
            else:  # name / company
                full_name = f"{c.get('firstName', '')} {c.get('lastName', '')}".lower()
                company   = (c.get("companyName") or "").lower()
                match = search in full_name or search in company
            if match:
                results.append(c)
        return results

    # ------------------------------------------------------------------
    # Token file helpers
    # ------------------------------------------------------------------

    def _token_file_path(self) -> Path:
        """
        Return the Path to .tokens.json in the project root, resolved relative
        to this file's location so it works regardless of Langflow's CWD.
        """
        return Path(__file__).parent.parent.parent.parent / ".tokens.json"

    def _load_token_file(self) -> dict:
        """
        Read .tokens.json and return its contents.
        File format: { "access_token": "...", "refresh_token": "..." }
        Returns an empty dict if the file doesn't exist or can't be parsed.
        """
        try:
            return json.loads(self._token_file_path().read_text())
        except Exception:
            return {}

    def _save_token_file(self, access_token: str, refresh_token: str) -> None:
        """
        Write both tokens to .tokens.json, overwriting the previous values.
        Silently ignores write errors so a permission issue doesn't crash the flow.
        """
        try:
            self._token_file_path().write_text(json.dumps({
                "access_token":  access_token,
                "refresh_token": refresh_token,
            }, indent=2))
        except Exception:
            pass

    def _active_token(self) -> str:
        """
        Return the best available access token using a two-level priority:
          1. In-process cache (_TOKEN_CACHE) — fastest, set after any refresh this session
          2. Token file (.tokens.json) — survives server restarts

        Also populates _TOKEN_CACHE from the file on a cache miss so that
        subsequent calls within the same process skip the file read.
        Raises RuntimeError if no token is found in either source.
        """
        cached = _TOKEN_CACHE.get("access_token")
        if cached:
            return cached

        file_entry = self._load_token_file()
        if file_entry.get("access_token"):
            _TOKEN_CACHE["access_token"] = file_entry["access_token"]
            return file_entry["access_token"]

        raise RuntimeError(
            "No access_token found in .tokens.json. "
            "Authorise the Jobber app and write the initial tokens to the file."
        )

    def _is_auth_error(self, response: httpx.Response) -> bool:
        """
        Return True if the response indicates an authentication failure.

        Jobber can signal auth errors in two ways:
          - HTTP 401 Unauthorized (raises before this is called via raise_for_status)
          - HTTP 200 with a GraphQL errors array containing an auth message
        """
        if response.status_code == 401:
            return True
        try:
            errors = response.json().get("errors", [])
            return any(
                "unauthorized" in str(e).lower()
                or "unauthenticated" in str(e).lower()
                or "not authenticated" in str(e).lower()
                for e in errors
            )
        except Exception:
            return False

    def _post(self, client: httpx.Client, token: str, payload: dict) -> httpx.Response:
        """
        Execute a single POST to the Jobber GraphQL endpoint.
        Does NOT call raise_for_status so callers can inspect auth errors
        before deciding to refresh.
        """
        return client.post(
            JOBBER_GRAPHQL_URL,
            headers=self._headers(token),
            json=payload,
        )

    def _run_with_token_refresh(self, fn, client: httpx.Client) -> list:
        """
        Execute fn(client, token) with automatic token refresh on auth failure.

        Detection strategy (handles both failure modes Jobber uses):
          1. HTTP 401  → immediate refresh + retry
          2. HTTP 200 with GraphQL auth error → refresh + retry
          3. Empty result due to expiry (edge case) → refresh + retry

        The refreshed token is stored in _TOKEN_CACHE so subsequent calls
        within this server process skip the refresh round-trip.

        Only retries once — if the refreshed token also fails, the error
        is propagated to the caller.
        """
        file_entry  = self._load_token_file()
        can_refresh = bool(file_entry.get("refresh_token") and self.client_id and self.client_secret)
        token  = self._active_token()
        result = fn(client, token)

        # Detect auth failure: empty list OR auth error flag on the raw response
        needs_refresh = (result == [] or getattr(result, "_auth_error", False))

        if needs_refresh and can_refresh:
            token  = self._refresh_access_token(client)
            result = fn(client, token)

        return result

    # ------------------------------------------------------------------
    # Entry point (called by Langflow and by to_toolkit when used as a tool)
    # ------------------------------------------------------------------

    def build_output(self) -> Message:
        """
        Main execution method.

        Flow:
          1. Resolve token: check _TOKEN_CACHE first, then .tokens.json file.
          2. Detect search type from search_value (or "none" if blank).
          3. If "id" → use _execute_by_id (single-record lookup, fastest).
             Otherwise → use _execute_all then client-side filter.
          4. Both paths go through _run_with_token_refresh which detects HTTP 401
             and GraphQL auth errors, refreshes via refresh_token, and retries once.
          5. Format results into a readable string and return as Message.
        """
        search      = self.search_value.strip() if self.search_value else ""
        search_type = _detect_search_type(search) if search else "none"

        with httpx.Client(timeout=15.0) as client:
            if search_type == "id":
                # Direct lookup by ID — skip fetching all clients
                clients = self._run_with_token_refresh(self._execute_by_id, client)
            else:
                # Fetch up to 50 clients then filter client-side
                all_clients = self._run_with_token_refresh(self._execute_all, client)
                clients     = self._filter_clients(all_clients, search_type) if search else all_clients

        # Format output
        if not clients:
            label = f" ({search_type}: '{search}')" if search else ""
            text  = f"No clients found{label}."
        else:
            lines = [f"Clients ({len(clients)}) — filter: {search_type}:"]
            for c in clients:
                name          = f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
                company       = c.get("companyName") or ""
                primary_email = next((e["address"] for e in c.get("emails", []) if e.get("primary")), "")
                primary_phone = next((p["number"]  for p in c.get("phones",  []) if p.get("primary")), "")
                lines.append(
                    f"- {name}{' | ' + company if company else ''}"
                    f" | {primary_email} | {primary_phone} | ID: {c['id']}"
                )
            text = "\n".join(lines)

        message      = Message(text=text)
        self.status  = message
        return message
