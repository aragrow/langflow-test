from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.message import Message

import httpx


class GitHubUser(Component):
    display_name = "GitHub User"
    description = "Fetch GitHub user info by username."
    icon = "Github"
    name = "GitHubUser"

    inputs = [
        MessageTextInput(
            name="username",
            display_name="Username",
            info="The GitHub username to look up",
            tool_mode=True,
        ),
    ]

    outputs = [
        Output(display_name="User Data", name="output", method="build_output"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    def build_output(self) -> Message:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"https://api.github.com/users/{self.username}")
            response.raise_for_status()
            data = response.json()

        summary = (
            f"{data.get('name') or self.username}: {data.get('public_repos', 0)} repos, "
            f"{data.get('followers', 0)} followers. Bio: {str(data.get('bio', ''))[:100]}"
        )
        message = Message(text=summary)
        self.status = message
        return message
