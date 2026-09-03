"""Travel assistant agent on AgentCore Runtime with OTel spans exported to S3."""

import os
import json
import random

from strands import Agent, tool
from strands.models.openai import OpenAIModel
from strands.telemetry import StrandsTelemetry
from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
import opentelemetry.trace as trace_api
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from s3_span_exporter import S3SpanExporter

# ── Configuration ────────────────────────────────────────────────────────────
SPAN_BUCKET = os.environ.get("SPAN_BUCKET", "agentcore-trace-demo-spans")
SPAN_PREFIX = os.environ.get("SPAN_PREFIX", "spans")

ALLOWED_MODELS = {
    "sol": "us.openai.gpt-5.6-sol",
    "terra": "us.openai.gpt-5.6-terra",
    "luna": "us.openai.gpt-5.6-luna",
}
MODEL_VARIANT = os.environ.get("MODEL_VARIANT", "terra").lower()
MODEL_ID = ALLOWED_MODELS.get(MODEL_VARIANT)
if not MODEL_ID:
    raise ValueError(f"MODEL_VARIANT must be one of {list(ALLOWED_MODELS.keys())}, got '{MODEL_VARIANT}'")

BEDROCK_API_KEY = os.environ.get("BEDROCK_API_KEY", "")
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1"))
BEDROCK_ENDPOINT = f"https://bedrock-runtime.{BEDROCK_REGION}.amazonaws.com/openai/v1"

if not BEDROCK_API_KEY:
    raise ValueError("BEDROCK_API_KEY is required. Create one in the Amazon Bedrock console → API keys.")

# ── OTel setup: add S3SpanExporter to existing or new provider ───────────────
s3_exporter = S3SpanExporter(bucket_name=SPAN_BUCKET, prefix=SPAN_PREFIX)
s3_processor = BatchSpanProcessor(s3_exporter)

global_provider = trace_api.get_tracer_provider()
real_provider = getattr(global_provider, "_real_provider", global_provider)

if isinstance(real_provider, SDKTracerProvider):
    # Auto-instrumentation already set up a provider (AgentCore Runtime case)
    real_provider.add_span_processor(s3_processor)
    print(f"[S3SpanExporter] Attached to existing TracerProvider")
else:
    # No auto-instrumentation (local dev case) — create our own
    provider = SDKTracerProvider()
    provider.add_span_processor(s3_processor)
    trace_api.set_tracer_provider(provider)
    StrandsTelemetry(tracer_provider=provider)
    print(f"[S3SpanExporter] Created new TracerProvider")

# ── Tools ────────────────────────────────────────────────────────────────────
FLIGHTS_DB = {
    "Tokyo": [
        {"airline": "ANA", "flight": "NH101", "price": 850, "duration": "11h30m"},
        {"airline": "JAL", "flight": "JL002", "price": 920, "duration": "11h15m"},
    ],
    "Paris": [
        {"airline": "Air France", "flight": "AF065", "price": 680, "duration": "8h45m"},
        {"airline": "Delta", "flight": "DL264", "price": 720, "duration": "9h10m"},
    ],
    "Sydney": [
        {"airline": "Qantas", "flight": "QF12", "price": 1100, "duration": "15h20m"},
    ],
}

HOTELS_DB = {
    "Tokyo": [
        {"name": "Park Hyatt Tokyo", "price_per_night": 450, "rating": 4.8},
        {"name": "Shinjuku Granbell", "price_per_night": 120, "rating": 4.2},
    ],
    "Paris": [
        {"name": "Le Marais Boutique", "price_per_night": 280, "rating": 4.5},
        {"name": "Ibis Bastille", "price_per_night": 95, "rating": 3.9},
    ],
    "Sydney": [
        {"name": "Four Seasons Sydney", "price_per_night": 520, "rating": 4.9},
        {"name": "YHA Sydney Harbour", "price_per_night": 45, "rating": 4.0},
    ],
}


@tool
def search_flights(destination: str) -> str:
    """Search available flights to a destination city.

    Args:
        destination: The destination city name (e.g. Tokyo, Paris, Sydney).
    """
    flights = FLIGHTS_DB.get(destination)
    if not flights:
        return json.dumps({"error": f"No flights found for {destination}. Available: {list(FLIGHTS_DB.keys())}"})
    return json.dumps({"destination": destination, "flights": flights})


@tool
def search_hotels(destination: str, nights: int) -> str:
    """Search available hotels in a destination city.

    Args:
        destination: The destination city name.
        nights: Number of nights to stay.
    """
    hotels = HOTELS_DB.get(destination)
    if not hotels:
        return json.dumps({"error": f"No hotels found for {destination}. Available: {list(HOTELS_DB.keys())}"})
    results = []
    for h in hotels:
        results.append({**h, "total_cost": h["price_per_night"] * nights, "nights": nights})
    return json.dumps({"destination": destination, "hotels": results})


@tool
def check_weather(city: str) -> str:
    """Check current weather conditions for a city.

    Args:
        city: The city name to check weather for.
    """
    conditions = ["Sunny, 25°C", "Partly cloudy, 22°C", "Rainy, 18°C", "Clear, 28°C", "Overcast, 20°C"]
    return json.dumps({"city": city, "weather": random.choice(conditions), "forecast": "next 3 days: similar"})


# ── Model ───────────────────────────────────────────────────────────────────
import openai

openai_client = openai.OpenAI(api_key=BEDROCK_API_KEY, base_url=BEDROCK_ENDPOINT)
model = OpenAIModel(client=openai_client, model=MODEL_ID)
print(f"[Model] {MODEL_VARIANT} → {MODEL_ID} via {BEDROCK_ENDPOINT}")
SYSTEM_PROMPT = (
    "You are a travel assistant. Help users plan trips by searching flights, "
    "hotels, and checking weather. Always search for relevant information before "
    "giving recommendations. Provide a clear summary with prices."
)
TOOLS = [search_flights, search_hotels, check_weather]

# ── AgentCore Runtime entrypoint ─────────────────────────────────────────────
app = BedrockAgentCoreApp()


@app.entrypoint
def handle(payload):
    user_input = payload.get("prompt", "")
    req_agent = Agent(model=model, tools=TOOLS, system_prompt=SYSTEM_PROMPT)
    response = req_agent(user_input)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
