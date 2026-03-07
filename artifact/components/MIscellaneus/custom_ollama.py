from lfx.custom.custom_component.component import Component
from lfx.io import FloatInput, MessageTextInput, Output, StrInput
from lfx.schema.message import Message

from langchain_community.llms import Ollama


class OllamaLLM(Component):
    display_name = "Ollama LLM"
    description = "Runs a prompt against a local Ollama model and returns the response."
    icon = "Ollama"
    name = "OllamaLLM"

    inputs = [
        MessageTextInput(
            name="prompt",
            display_name="Prompt",
            info="The prompt to send to the Ollama model.",
            tool_mode=True,
        ),
        StrInput(
            name="model_name",
            display_name="Model Name",
            info="The name of the Ollama model to use.",
            value="granite4:latest",
        ),
        StrInput(
            name="base_url",
            display_name="Base URL",
            info="Endpoint of the Ollama API.",
            value="http://localhost:11434",
            advanced=True,
        ),
        FloatInput(
            name="temperature",
            display_name="Temperature",
            info="Controls randomness of the output.",
            value=0.7,
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Response", name="output", method="build_output"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    def build_output(self) -> Message:
        llm = Ollama(
            model=self.model_name,
            base_url=self.base_url,
            temperature=self.temperature,
        )
        response_text = llm.invoke(self.prompt)
        message = Message(text=response_text)
        self.status = message
        return message
