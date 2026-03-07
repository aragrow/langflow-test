from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

import httpx
import json


class CustomAPI(Component):
    display_name = "Custom API"
    description = "Calls a custom REST API endpoint with text input and returns the response."
    icon = "link"
    name = "CustomAPI"

    inputs = [
        MessageTextInput(
            name="input_text",
            display_name="Input Text",
            info="The text payload to send to the API",
            tool_mode=True,
        ),
        StrInput(
            name="api_url",
            display_name="API URL",
            info="Endpoint URL (e.g. http://localhost:8000/process)",
        ),
        SecretStrInput(
            name="auth_header",
            display_name="Authorization Header",
            info="Value for the Authorization header (e.g. Bearer your_token). Leave blank if not required.",
        ),
        StrInput(
            name="extra_headers",
            display_name="Extra Headers (JSON)",
            info='Additional headers as a JSON string, e.g. {"X-Custom": "value"}',
            advanced=True,
        ),
        IntInput(
            name="timeout",
            display_name="Timeout (s)",
            value=10,
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Response", name="output", method="build_output"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    def build_output(self) -> Message:
        headers = {"Content-Type": "text/plain"}
        if self.auth_header:
            headers["Authorization"] = self.auth_header
        if self.extra_headers:
            headers.update(json.loads(self.extra_headers))

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                self.api_url,
                content=self.input_text.encode("utf-8"),
                headers=headers,
            )
            response.raise_for_status()

        message = Message(text=response.text)
        self.status = message
        return message
