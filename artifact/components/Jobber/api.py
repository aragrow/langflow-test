from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

import httpx

JOBBER_GRAPHQL_URL = "https://api.getjobber.com/api/graphql"
JOBBER_TOKEN_URL = "https://api.getjobber.com/api/oauth/token"


class JobberAPI(Component):
    display_name = "Jobber API"
    description = "Executes a GraphQL query or mutation against the Jobber API using OAuth 2.0 authentication."
    documentation: str = "https://developer.getjobber.com/docs/"
    icon = "code"
    name = "JobberAPI"

    inputs = [
        MessageTextInput(
            name="query",
            display_name="GraphQL Query",
            info="The GraphQL query or mutation to execute against the Jobber API",
            tool_mode=True,
        ),
        SecretStrInput(
            name="access_token",
            display_name="Access Token",
            info="OAuth 2.0 access token. Expires after 60 minutes — use the refresh token to get a new one.",
        ),
        SecretStrInput(
            name="refresh_token",
            display_name="Refresh Token",
            info="OAuth 2.0 refresh token used to obtain a new access token when it expires.",
        ),
        SecretStrInput(
            name="client_id",
            display_name="Client ID",
            info="Your Jobber app's Client ID from the Developer Center.",
        ),
        SecretStrInput(
            name="client_secret",
            display_name="Client Secret",
            info="Your Jobber app's Client Secret from the Developer Center.",
        ),
    ]

    outputs = [
        Output(display_name="Response", name="output", method="build_output"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _refresh_access_token(self, client: httpx.Client) -> str:
        """Exchange the refresh token for a new access token."""
        response = client.post(
            JOBBER_TOKEN_URL,
            json={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def _execute_query(self, client: httpx.Client, token: str) -> dict:
        response = client.post(
            JOBBER_GRAPHQL_URL,
            headers=self._headers(token),
            json={"query": self.query},
        )
        response.raise_for_status()
        return response.json()

    def build_output(self) -> Message:
        with httpx.Client(timeout=15.0) as client:
            token = self.access_token
            data = self._execute_query(client, token)

            # If unauthorized, try refreshing the token
            if "errors" in data:
                errors = data["errors"]
                is_auth_error = any(
                    "unauthorized" in str(e).lower() or "401" in str(e)
                    for e in errors
                )
                if is_auth_error and self.refresh_token and self.client_id and self.client_secret:
                    token = self._refresh_access_token(client)
                    data = self._execute_query(client, token)

        import json
        text = json.dumps(data, indent=2)
        message = Message(text=text)
        self.status = message
        return message
