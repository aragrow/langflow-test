from lfx.custom.custom_component.component import Component
from lfx.io import MessageInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage


SYSTEM_PROMPT = """You are a helpful Customer Support Agent. Your role is to assist customers \
with their inquiries professionally and empathetically. Provide clear, concise, and helpful responses."""


class CustomerSupport(Component):
    display_name = "Customer Support"
    description = (
        "Handles all customer-facing inquiries and support requests, including: "
        "questions about services offered, pricing and quotes, business locations and hours, "
        "general information requests, complaints and issue resolution, billing and invoice questions, "
        "appointment scheduling and follow-ups, product or service recommendations, "
        "and any other customer support needs."
    )
    icon = "headphones"
    name = "CustomerSupport"

    inputs = [
        MessageInput(
            name="message",
            display_name="Message",
            info="Incoming message from the agent or flow",
        ),
        MessageTextInput(
            name="input_value",
            display_name="Customer Message",
            info="The customer's message or inquiry",
            tool_mode=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="Google API Key",
            info="Your Google Gemini API key",
        ),
        StrInput(
            name="model",
            display_name="Model",
            info="Gemini model to use",
            value="gemini-2.0-flash",
        ),
    ]

    outputs = [
        Output(display_name="Response", name="output", method="build_output"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    def build_output(self) -> Message:
        llm = ChatGoogleGenerativeAI(
            model=self.model,
            google_api_key=self.api_key,
        )
        user_message = self.message.text if self.message else self.input_value
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ]
        response = llm.invoke(messages)
        message = Message(text=response.content)
        self.status = message
        return message
