# =============================================================================
# Jobber – Authentication Test Component
# =============================================================================
# Purpose : Verifies that the tokens in .tokens.json are valid and can
#           authenticate against the Jobber GraphQL API.
#           If the access_token is expired, it will attempt a refresh using
#           the refresh_token and write both new tokens back to the file.
# Output  : "Authenticated as: <account name>" on success, or an error message.
# Template: See getClients.py for the full auth pattern used here.
#
# NOTE on tool schema: client_id and client_secret are intentionally NOT
# tool_mode=True. The single tool_mode input (_trigger) ensures the schema
# builder includes only that input, keeping credentials out of the tool schema
# and preserving their canvas-configured global variable values at call time.
# =============================================================================

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, SecretStrInput
from lfx.schema.message import Message

import httpx
import json
from pathlib import Path

# ---------------------------------------------------------------------------
# API constants
# ---------------------------------------------------------------------------
JOBBER_GRAPHQL_URL = "https://api.getjobber.com/api/graphql"
JOBBER_TOKEN_URL   = "https://api.getjobber.com/api/oauth/token"
JOBBER_VERSION     = "2023-11-15"

# ---------------------------------------------------------------------------
# Minimal query — just enough to confirm auth succeeds
# ---------------------------------------------------------------------------
_QUERY_ACCOUNT = """
query {
  account {
    id
    name
  }
}
"""


# ---------------------------------------------------------------------------
# Component
# ---------------------------------------------------------------------------

class JobberGetAuthenticated(Component):
    """
    Langflow component for testing Jobber OAuth 2.0 authentication.

    Reads tokens from .tokens.json. If the access_token is expired, it
    exchanges the refresh_token for a new pair using the canvas-configured
    client_id and client_secret (resolved from Langflow global variables).
    """

    display_name = "Jobber Authenticate"
    description  = (
        "Tests authentication against the Jobber API using tokens stored in "
        ".tokens.json. Automatically refreshes expired tokens and updates the file."
    )
    documentation: str = "https://developer.getjobber.com/docs/"
    icon = "shield-check"
    name = "JobberGetAuthenticated"

    # Class-level token cache — avoids repeated file reads within the same process.
    # Must be class-level (not module-level) because Langflow may exec the file
    # in a restricted namespace where module globals are not accessible.
    _TOKEN_CACHE: dict = {}

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------
    inputs = [
        # _trigger is the ONLY tool_mode=True input. This is required so that
        # to_toolkit() builds a schema from [_trigger] only, leaving client_id
        # and client_secret out of the tool schema entirely. Without this, the
        # fallback path includes ALL inputs and the LLM is prompted for credentials.
        MessageTextInput(
            name="_trigger",
            display_name="Trigger",
            info="Leave empty. Used internally to anchor the tool schema so credentials are excluded.",
            tool_mode=True,
            value="",
        ),
        SecretStrInput(
            name="client_id",
            display_name="Client ID",
            info="Jobber app Client ID. Assign a Langflow global variable here — never enter the value directly.",
            load_from_db=True,
            value="",
        ),
        SecretStrInput(
            name="client_secret",
            display_name="Client Secret",
            info="Jobber app Client Secret. Assign a Langflow global variable here — never enter the value directly.",
            load_from_db=True,
            value="",
        ),
    ]

    # ------------------------------------------------------------------
    # Outputs
    # ------------------------------------------------------------------
    outputs = [
        Output(display_name="Result", name="output", method="check_authentication"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    # ------------------------------------------------------------------
    # Token file helpers
    # ------------------------------------------------------------------

    def _token_file_path(self) -> Path:
        return Path(__file__).parent.parent.parent.parent / ".tokens.json"

    def _load_token_file(self) -> dict:
        try:
            return json.loads(self._token_file_path().read_text())
        except Exception:
            return {}

    def _save_token_file(self, access_token: str, refresh_token: str) -> None:
        try:
            self._token_file_path().write_text(json.dumps({
                "access_token":  access_token,
                "refresh_token": refresh_token,
            }, indent=2))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-JOBBER-GRAPHQL-VERSION": JOBBER_VERSION,
        }

    def _active_token(self) -> str:
        cached = self._TOKEN_CACHE.get("access_token")
        if cached:
            return cached
        file_entry = self._load_token_file()
        if file_entry.get("access_token"):
            self._TOKEN_CACHE["access_token"] = file_entry["access_token"]
            return file_entry["access_token"]
        raise RuntimeError(
            "CONFIGURATION ERROR: .tokens.json is not set up. "
            "An administrator must populate this file before this tool can be used. "
            "Do not ask the user for credentials — this is a server-side configuration issue."
        )

    def _is_auth_error(self, response: httpx.Response) -> bool:
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

    def _refresh_access_token(self, client: httpx.Client) -> str:
        file_entry    = self._load_token_file()
        refresh_token = file_entry.get("refresh_token")
        if not refresh_token:
            raise RuntimeError(
                "No refresh_token found in .tokens.json. "
                "Re-authorise the Jobber app and write both tokens to the file."
            )
        print(f"[JobberGetAuthenticated] client_id='{self.client_id}' secret_len={len(self.client_secret or '')}", flush=True)
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
        self._TOKEN_CACHE["access_token"] = new_token
        self._save_token_file(new_token, new_refresh)
        return new_token

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def check_authentication(self) -> Message:
        """
        Attempt a minimal GraphQL query to confirm the token is valid.
        If the token is expired, refresh it and retry once.
        Returns a success message with the account name, or an error message.
        """
        token_file = self._token_file_path()
        print(f"\n[JobberGetAuthenticated] token_file={token_file} exists={token_file.exists()}", flush=True)

        try:
            token = self._active_token()
        except RuntimeError as e:
            print(f"[JobberGetAuthenticated] no token: {e}", flush=True)
            message = Message(text=f"Auth error: {e}")
            self.status = message
            return message

        with httpx.Client(timeout=15.0) as client:
            response = client.post(
                JOBBER_GRAPHQL_URL,
                headers=self._headers(token),
                json={"query": _QUERY_ACCOUNT},
            )
            print(f"[JobberGetAuthenticated] response status={response.status_code}", flush=True)

            if self._is_auth_error(response):
                print("[JobberGetAuthenticated] auth error, attempting token refresh", flush=True)
                try:
                    token    = self._refresh_access_token(client)
                    response = client.post(
                        JOBBER_GRAPHQL_URL,
                        headers=self._headers(token),
                        json={"query": _QUERY_ACCOUNT},
                    )
                    print(f"[JobberGetAuthenticated] retry status={response.status_code}", flush=True)
                except Exception as e:
                    message = Message(text=f"Token refresh failed: {e}")
                    self.status = message
                    return message

        if self._is_auth_error(response):
            message = Message(text="Authentication failed even after token refresh.")
            self.status = message
            return message

        response.raise_for_status()
        account = response.json().get("data", {}).get("account", {})
        text    = f"Authenticated as: {account.get('name', 'unknown')} (ID: {account.get('id', '?')})"
        print(f"[JobberGetAuthenticated] {text}", flush=True)

        message     = Message(text=text)
        self.status = message
        return message
