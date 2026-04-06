# =============================================================================
# Jobber – Get Jobs Component
# =============================================================================
# Purpose : Retrieves jobs for a Jobber client via the GraphQL API.
# Auth    : OAuth 2.0 – tokens are read from a .tokens.json file.
#           The file is updated automatically when a token refresh occurs
#           (Jobber access tokens expire after 60 minutes).
# Input   : client_id_jobber — the Jobber client ID (base64) from Get Clients.
# Output  : Human-readable job list with title, status, dates, and total.
#           Also exposes a Toolset output so it can be wired to an agent.
#
# Jobber API:
# - POST https://api.getjobber.com/api/graphql — GraphQL query
# - POST https://api.getjobber.com/api/oauth/token — token refresh
#
# Inputs (configured in Langflow):
# - client_id_jobber : Jobber client ID from Get Clients result (tool_mode)
# - client_id        : Jobber app Client ID for token refresh (secret)
# - client_secret    : Jobber app Client Secret for token refresh (secret)
#
# Outputs:
# - output            : Message listing the client's jobs
# - component_as_tool : Exposes this component as a tool for agents
# =============================================================================

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, SecretStrInput
from lfx.schema.message import Message

import httpx
import json
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# API constants
# ---------------------------------------------------------------------------
JOBBER_GRAPHQL_URL = "https://api.getjobber.com/api/graphql"
JOBBER_TOKEN_URL   = "https://api.getjobber.com/api/oauth/token"
JOBBER_VERSION     = "2026-03-10"

# ---------------------------------------------------------------------------
# In-process token cache
# ---------------------------------------------------------------------------
_TOKEN_CACHE: dict[str, str] = {}

# ---------------------------------------------------------------------------
# GraphQL query
# ---------------------------------------------------------------------------
_QUERY_JOBS = """
query GetJobs($clientId: ID!, $cursor: String) {
  client(id: $clientId) {
    jobs(after: $cursor, first: 50) {
      nodes {
        id
        title
        jobStatus
        startAt
        endAt
        total
        property {
          id
          address {
            street
            city
            province
          }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


class JobberGetJobs(Component):
    """
    Langflow component that fetches jobs for a Jobber client via the GraphQL API.

    Authentication follows the same pattern as JobberGetClients:
    tokens from .tokens.json with automatic refresh on expiry.
    """

    display_name = "Jobber Get Jobs"
    description = (
        "Retrieves jobs for a Jobber client. "
        "Pass the Jobber client ID from a previous Get Clients lookup."
    )
    documentation: str = "https://developer.getjobber.com/docs/"
    icon = "briefcase"
    name = "JobberGetJobs"

    inputs = [
        MessageTextInput(
            name="client_id_jobber",
            display_name="Jobber Client ID",
            info=(
                "The Jobber client ID (base64 string from Get Clients result, "
                "e.g. Q2xpZW50OjEyMzQ=)."
            ),
            tool_mode=True,
        ),
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

    outputs = [
        Output(display_name="Response", name="output", method="get_jobs"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    # ------------------------------------------------------------------
    # Private helpers (same pattern as getClients.py)
    # ------------------------------------------------------------------

    def _headers(self, token: str) -> dict:
        """Build required headers for Jobber GraphQL requests."""
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-JOBBER-GRAPHQL-VERSION": JOBBER_VERSION,
        }

    def _refresh_access_token(self, client: httpx.Client) -> str:
        """Exchange refresh_token for a new access_token. Updates cache and file."""
        file_entry = self._load_token_file()
        refresh_token = file_entry.get("refresh_token")
        if not refresh_token:
            raise RuntimeError(
                "No refresh_token found in token file. "
                "Re-authorise the Jobber app and write both tokens to the file."
            )

        response = client.post(
            JOBBER_TOKEN_URL,
            json={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        new_token = payload["access_token"]
        new_refresh = payload.get("refresh_token", refresh_token)

        _TOKEN_CACHE["access_token"] = new_token
        self._save_token_file(new_token, new_refresh)
        return new_token

    def _token_file_path(self) -> Path:
        """Return the Path to .tokens.json in the project root."""
        return Path(__file__).parent.parent.parent.parent / ".tokens.json"

    def _load_token_file(self) -> dict:
        """Read .tokens.json and return its contents."""
        try:
            return json.loads(self._token_file_path().read_text())
        except Exception:
            return {}

    def _save_token_file(self, access_token: str, refresh_token: str) -> None:
        """Write both tokens to .tokens.json."""
        try:
            self._token_file_path().write_text(json.dumps({
                "access_token": access_token,
                "refresh_token": refresh_token,
            }, indent=2))
        except Exception:
            pass

    def _active_token(self) -> str:
        """Return the best available access token (cache → file)."""
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
        """Return True if the response indicates an authentication failure."""
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
        """Execute a single POST to the Jobber GraphQL endpoint."""
        return client.post(
            JOBBER_GRAPHQL_URL,
            headers=self._headers(token),
            json=payload,
        )

    def _run_with_token_refresh(self, fn, client: httpx.Client) -> list:
        """Execute fn(client, token) with automatic token refresh on auth failure."""
        file_entry = self._load_token_file()
        can_refresh = bool(file_entry.get("refresh_token") and self.client_id and self.client_secret)
        token = self._active_token()
        result = fn(client, token)

        needs_refresh = (result == [] or getattr(result, "_auth_error", False))
        if needs_refresh and can_refresh:
            token = self._refresh_access_token(client)
            result = fn(client, token)
        return result

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------

    def _execute_jobs(self, client: httpx.Client, token: str) -> list:
        """Fetch jobs for a client via the GraphQL API."""
        response = self._post(
            client, token,
            {"query": _QUERY_JOBS, "variables": {"clientId": self.client_id_jobber.strip(), "cursor": None}},
        )
        if self._is_auth_error(response):
            result = []
            result._auth_error = True  # type: ignore[attr-defined]
            return result
        response.raise_for_status()
        return response.json().get("data", {}).get("client", {}).get("jobs", {}).get("nodes", [])

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_date(iso_str: str) -> str:
        """Convert ISO datetime to a short human-readable date."""
        if not iso_str:
            return ""
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return dt.strftime("%b %d, %Y")
        except (ValueError, AttributeError):
            return iso_str

    @staticmethod
    def _format_currency(amount) -> str:
        """Format a numeric amount as currency."""
        if amount is None:
            return ""
        try:
            return f"${float(amount):,.2f}"
        except (ValueError, TypeError):
            return str(amount)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def get_jobs(self) -> Message:
        """Fetch and return jobs for the specified Jobber client.

        Flow:
        1. Resolve token from cache or .tokens.json.
        2. Query Jobber for the client's jobs (up to 50).
        3. Format results with title, status, dates, total, and property.
        """
        jobber_id = (self.client_id_jobber or "").strip()
        if not jobber_id:
            return self._msg("Error: A Jobber client ID is required.")

        try:
            with httpx.Client(timeout=15.0) as client:
                jobs = self._run_with_token_refresh(self._execute_jobs, client)
        except RuntimeError as exc:
            return self._msg(f"Error: {exc}")
        except httpx.HTTPStatusError as exc:
            return self._msg(
                f"Jobber API error: {exc.response.status_code} — {exc.response.text[:200]}"
            )

        if not jobs:
            return self._msg("No jobs found for this client.")

        lines = [f"Jobs ({len(jobs)}):"]
        for i, job in enumerate(jobs, 1):
            title = job.get("title", "Untitled")
            status = job.get("jobStatus", "unknown")
            start = self._format_date(job.get("startAt", ""))
            end = self._format_date(job.get("endAt", ""))
            total = self._format_currency(job.get("total"))
            job_id = job.get("id", "unknown")

            # Property address
            prop = job.get("property") or {}
            addr = prop.get("address", {})
            prop_parts = [addr.get("street", ""), addr.get("city", ""), addr.get("province", "")]
            prop_str = ", ".join(p for p in prop_parts if p)

            date_str = f"{start} – {end}" if start and end else start or end or "No dates"
            total_str = f" | {total}" if total else ""
            prop_line = f" | Property: {prop_str}" if prop_str else ""

            lines.append(
                f"  {i}. {title}\n"
                f"     Status: {status} | {date_str}{total_str}\n"
                f"     ID: {job_id}{prop_line}"
            )

        text = "\n".join(lines)
        return self._msg(text)

    def _msg(self, text: str) -> Message:
        message = Message(text=text)
        self.status = message
        return message
