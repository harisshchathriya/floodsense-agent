import os
from pathlib import Path

from dotenv import load_dotenv
from strands import Agent, tool
from strands.models.openai import OpenAIModel

from tools.rainfall import get_rainfall
from tools.elevation import get_elevation


# --------------------------------------------------
# 1. Load environment variables
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(ENV_FILE)

api_key = os.getenv("OPENROUTER_API_KEY")
model_id = os.getenv("OPENROUTER_MODEL", "openrouter/free")

if not api_key:
    raise RuntimeError(
        "OPENROUTER_API_KEY is missing. "
        "Check the .env file in the project root."
    )


# --------------------------------------------------
# 2. Configure the OpenRouter model for Strands
# --------------------------------------------------

model = OpenAIModel(
    client_args={
        "api_key": api_key,
        "base_url": "https://openrouter.ai/api/v1",
    },
    model_id=model_id,
    params={
        "max_tokens": 900,
        "temperature": 0.1,
    },
)


# --------------------------------------------------
# 3. Define the rainfall tool
# --------------------------------------------------

@tool
def get_location_rainfall(latitude: float, longitude: float) -> dict:
    """
    Retrieve current and hourly rainfall for a user-selected location.
    Latitude must be between -90 and 90.
    Longitude must be between -180 and 180.
    """
    return get_rainfall(latitude, longitude)


# --------------------------------------------------
# 4. Define the terrain elevation tool
# --------------------------------------------------

@tool
def get_location_elevation(latitude: float, longitude: float) -> dict:
    """
    Retrieve terrain elevation for a user-selected location.
    Latitude must be between -90 and 90.
    Longitude must be between -180 and 180.
    """
    return get_elevation(latitude, longitude)


# --------------------------------------------------
# 5. Create the FloodSense agent
# --------------------------------------------------

agent = Agent(
    model=model,
    tools=[
        get_location_rainfall,
        get_location_elevation,
    ],
    system_prompt=(
        "You are FloodSense, a flood-awareness assistant for "
        "Tiruchirappalli, Tamil Nadu, India. "
        "Use the latitude and longitude supplied in the user's "
        "request when calling the location rainfall and elevation tools. "
        "Never substitute fixed coordinates when coordinates are supplied. "
        "When both rainfall and elevation are requested, call both tools "
        "with the same supplied coordinates. "
        "Report returned values accurately, including units, timestamps, "
        "locations, and sources. Never invent measurements. "
        "Rainfall and elevation alone cannot establish a reliable flood-risk "
        "level. Do not label an area safe or dangerous based only on these "
        "measurements. "
        "If a tool fails, clearly state that its data could not be retrieved. "
        "Keep responses concise and factual."
    ),
)


# --------------------------------------------------
# 6. Helper function
# --------------------------------------------------

def ask_floodsense(question: str) -> str:
    """
    Send a question to FloodSense and return the response.
    """
    if not question or not question.strip():
        raise ValueError("Question cannot be empty.")

    result = agent(question)
    return str(result)


# --------------------------------------------------
# 7. Run a test
# --------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("FloodSense Agent: Rainfall and Elevation Test")
    print("=" * 60)

    question = (
        "Retrieve both current rainfall data and terrain "
        "elevation for central Tiruchirappalli using the "
        "available tools. Report precipitation in millimetres, "
        "rain in millimetres, the observation timestamp, "
        "terrain elevation in metres, and the data sources. "
        "Keep the summary concise. Do not infer a flood-risk "
        "level from these measurements alone."
    )

    try:
        response = ask_floodsense(question)

        print("\nFloodSense Response:")
        print("-" * 60)
        print(response)

    except Exception as exc:
        print("\nFloodSense test failed.")
        print(f"Error type: {type(exc).__name__}")
        print(f"Details: {exc}")
        print(
            "\nCheck your OpenRouter model availability, "
            "API quota, network connection, and tool APIs."
        )