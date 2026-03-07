from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, SecretStrInput
from lfx.schema.message import Message

import httpx


class OpenWeather(Component):
    display_name = "OpenWeather"
    description = "Get current weather conditions for a city using the OpenWeatherMap API."
    icon = "CloudRain"
    name = "OpenWeather"

    inputs = [
        MessageTextInput(
            name="city",
            display_name="City",
            info="City name to get weather for",
            value="Minneapolis",
            tool_mode=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="API Key",
            info="Your OpenWeatherMap API key",
        ),
    ]

    outputs = [
        Output(display_name="Weather", name="output", method="build_output"),
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"]),
    ]

    def build_output(self) -> Message:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={"q": self.city, "appid": self.api_key, "units": "imperial"},
            )
            response.raise_for_status()
            data = response.json()

        temp = data["main"]["temp"]
        desc = data["weather"][0]["description"]
        text = f"Weather in {self.city}: {temp}°F, {desc.title()}"
        message = Message(text=text)
        self.status = message
        return message
