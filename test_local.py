"""Local test: run the travel agent and verify spans land in S3."""

import os
import json
import time
import random

import boto3
from strands import Agent, tool
from strands.models import BedrockModel
from strands.telemetry import StrandsTelemetry
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
import opentelemetry.trace as trace_api

from s3_span_exporter import S3SpanExporter

# ── Config ───────────────────────────────────────────────────────────────────
SPAN_BUCKET = os.environ.get("SPAN_BUCKET", "agentcore-trace-demo-spans")
SPAN_PREFIX = os.environ.get("SPAN_PREFIX", "spans")
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
REGION = boto3.Session().region_name

# ── OTel → S3 ────────────────────────────────────────────────────────────────
s3_exporter = S3SpanExporter(bucket_name=SPAN_BUCKET, prefix=SPAN_PREFIX, region=REGION)
provider = TracerProvider()
processor = BatchSpanProcessor(s3_exporter)
provider.add_span_processor(processor)
trace_api.set_tracer_provider(provider)
StrandsTelemetry(tracer_provider=provider)

# ── Tools (inline to avoid importing travel_agent.py module-level side effects) ──
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


# ── Agent ────────────────────────────────────────────────────────────────────
model = BedrockModel(model_id=MODEL_ID)
agent = Agent(
    model=model,
    tools=[search_flights, search_hotels, check_weather],
    system_prompt=(
        "You are a travel assistant. Help users plan trips by searching flights, "
        "hotels, and checking weather. Always search for relevant information before "
        "giving recommendations. Provide a clear summary with prices."
    ),
)

# ── Run ──────────────────────────────────────────────────────────────────────
print("=" * 60)
print("  Local test: Travel Agent → OTel spans → S3")
print("=" * 60)

prompt = "I want to plan a 5-night trip to Tokyo. Find me flights, hotels, and check the weather."
print(f"\nPrompt: {prompt}\n")

response = agent(prompt)
print("\n--- Agent Response ---")
print(response.message["content"][0]["text"])

# Flush spans to S3
print("\nFlushing spans to S3...")
processor.force_flush()
provider.shutdown()
time.sleep(2)

# Verify spans in S3
print(f"\nChecking S3 bucket: {SPAN_BUCKET}/{SPAN_PREFIX}/")
s3 = boto3.client("s3", region_name=REGION)
resp = s3.list_objects_v2(Bucket=SPAN_BUCKET, Prefix=SPAN_PREFIX + "/", MaxKeys=10)
objects = resp.get("Contents", [])

if objects:
    print(f"Found {len(objects)} span file(s):")
    for obj in objects:
        print(f"  s3://{SPAN_BUCKET}/{obj['Key']}  ({obj['Size']} bytes)")
    latest = sorted(objects, key=lambda o: o["LastModified"])[-1]
    body = s3.get_object(Bucket=SPAN_BUCKET, Key=latest["Key"])["Body"].read()
    spans = json.loads(body)
    print(f"\nLatest file contains {len(spans)} span(s):")
    for sp in spans:
        dur = f"{sp['duration_ms']:.0f}ms" if sp.get("duration_ms") else "?"
        print(f"  [{dur}] {sp['name']}")
else:
    print("No span files found — check bucket name and IAM permissions.")

print("\nDone.")
