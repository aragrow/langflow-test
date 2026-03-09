from lfx.custom.custom_component.component import Component
from lfx.io import DropdownInput, MessageTextInput, Output, StrInput
from lfx.schema.message import Message

import re


class RegexRouter(Component):
    display_name = "Regex Router"
    description = (
        "Routes messages based on pattern matching. "
        "Use Match/No Match outputs for conditional flow branching, "
        "or the Result output for string-based evaluation."
    )
    icon = "regex"
    name = "RegexRouter"

    inputs = [
        MessageTextInput(
            name="text",
            display_name="Text",
            info="Text to evaluate",
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
        Output(display_name="Match", name="true_response", method="build_true_response"),
        Output(display_name="No Match", name="false_response", method="build_false_response"),
        Output(display_name="Result", name="output", method="build_output"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    def _evaluate(self) -> bool:
        text = self.text
        pattern = self.pattern
        if self.operator == "equals":
            return text == pattern
        elif self.operator == "contains":
            return pattern in text
        else:
            return re.search(pattern, text) is not None

    def build_true_response(self) -> Message:
        """Fires only when the pattern matches. Use this output to continue the flow on success."""
        if not self._evaluate():
            self.stop("true_response")
        return Message(text=self.text, sender="User", sender_name="User")

    def build_false_response(self) -> Message:
        """Fires only when the pattern does NOT match. Use this output to handle failure cases."""
        if self._evaluate():
            self.stop("false_response")
        return Message(text=self.text, sender="User", sender_name="User")

    def build_output(self) -> Message:
        """Returns 'true' or 'false' as text. Legacy single-output mode."""
        result = "true" if self._evaluate() else "false"
        message = Message(text=result)
        self.status = message
        return message
