from lfx.custom.custom_component.component import Component
from lfx.io import DropdownInput, MessageTextInput, Output, StrInput
from lfx.schema.message import Message

import re


class RegexRouter(Component):
    display_name = "Regex Router"
    description = "Evaluates text against a pattern using equals, contains, or regex matching. Returns match result."
    icon = "regex"
    name = "RegexRouter"

    inputs = [
        MessageTextInput(
            name="text",
            display_name="Text",
            info="Text to evaluate",
            tool_mode=True,
        ),
        DropdownInput(
            name="operator",
            display_name="Operator",
            options=["equals", "contains", "regex"],
            value="equals",
        ),
        StrInput(
            name="pattern",
            display_name="Pattern",
            info="The value or regex pattern to match against",
        ),
    ]

    outputs = [
        Output(display_name="Result", name="output", method="build_output"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    def build_output(self) -> Message:
        text = self.text
        pattern = self.pattern

        if self.operator == "equals":
            matched = text == pattern
        elif self.operator == "contains":
            matched = pattern in text
        else:
            matched = re.search(pattern, text) is not None

        result = "true" if matched else "false"
        message = Message(text=result)
        self.status = message
        return message
