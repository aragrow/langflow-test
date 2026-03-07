from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.message import Message


POSITIVE = {"good", "great", "awesome", "love", "fantastic", "happy"}
NEGATIVE = {"bad", "terrible", "hate", "awful", "sad", "angry"}


class SimpleSentiment(Component):
    display_name = "Simple Sentiment"
    description = "Naive rule-based sentiment classifier. Returns 'positive', 'negative', or 'neutral'."
    icon = "Smile"
    name = "SimpleSentiment"

    inputs = [
        MessageTextInput(
            name="text",
            display_name="Text",
            info="Text to classify",
            tool_mode=True,
        ),
    ]

    outputs = [
        Output(display_name="Sentiment", name="output", method="build_output"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    def build_output(self) -> Message:
        words = self.text.lower().split()
        score = sum(1 for w in words if w in POSITIVE) - sum(1 for w in words if w in NEGATIVE)

        if score > 0:
            label = "positive"
        elif score < 0:
            label = "negative"
        else:
            label = "neutral"

        message = Message(text=label)
        self.status = message
        return message
