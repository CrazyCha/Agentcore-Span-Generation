# AgentCore Trace Span → S3 Demo

A proof-of-concept that exports OpenTelemetry trace spans from an [Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/) Runtime agent **directly to S3**, bypassing CloudWatch/ADOT. Designed for scenarios where a downstream data engine consumes raw span data from S3 at high throughput.

## What It Does

```
                      ┌─────────────────────────────────────────┐
                      │         AgentCore Runtime (ARM64)       │
User ── invoke ──────▶│                                         │
                      │  Strands Agent (travel assistant)       │
                      │    ├─ search_flights                    │
                      │    ├─ search_hotels                     │
                      │    └─ check_weather                     │
                      │                                         │
                      │  OTel TracerProvider                    │
                      │    ├─ CloudWatch (default, auto)        │
                      │    └─ S3SpanExporter (custom)  ────────▶│──▶ S3 Bucket
                      └─────────────────────────────────────────┘
                                                                     spans/
                                                                       {session_id}/
                                                                         {trace_id}/
                                                                           {ts}-{uuid}.json
```

- **S3SpanExporter** — a custom OTel `SpanExporter` that calls `s3:PutObject` directly from the agent process. No sidecar, no collector, no extra service.
- **Dual-write** — appends to the auto-instrumented TracerProvider so CloudWatch traces are preserved alongside S3 export.
- **Per-request Agent isolation** — each invocation gets its own `Agent` instance, safe for concurrent sessions.
- **Concurrent user simulation** — included script to simulate N users running multi-turn conversations in parallel.

## Prerequisites

- AWS account with Bedrock AgentCore access (us-east-1)
- Bedrock model access for Claude Haiku (`us.anthropic.claude-haiku-4-5-20251001-v1:0`)
- Python 3.10+
- AWS credentials configured (`aws configure` or IAM role)

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
pip install bedrock-agentcore-starter-toolkit
```

### 2. Create the S3 bucket

```bash
aws s3 mb s3://agentcore-trace-demo-spans --region us-east-1
```

### 3. Deploy to AgentCore Runtime

```bash
python deploy.py
```

This will:
- Build and push a container image via CodeBuild (ARM64)
- Create/update the AgentCore Runtime agent
- Attach S3 write permissions to the execution role
- Save deployment info to `deploy-output.json`

Deployment takes ~2 minutes.

### 4. Test with a single invocation

```bash
python invoke_remote.py "Plan a 3-night trip to Tokyo"
```

The script invokes the agent, prints the response, then checks S3 for new span files.

### 5. Simulate concurrent users

```bash
# 5 concurrent users, 3 conversation turns each
python simulate_users.py --users 5 --turns 3

# 10 users, full 4-turn conversations, no delay between turns
python simulate_users.py --users 10 --delay 0
```

| Flag | Description | Default |
|------|-------------|---------|
| `--users N` | Number of concurrent user sessions | 3 |
| `--turns M` | Max conversation turns per user | Full script (4 turns) |
| `--delay S` | Seconds between turns within a session | 1.0 |
| `--skip-s3-check` | Skip the S3 span summary at the end | Off |

After running, the script prints:
- Per-user latency and success/failure stats
- S3 span file count, total spans, and unique session IDs

### 6. Local testing (no deployment needed)

```bash
python test_local.py
```

Runs the agent locally, writes spans to S3, and verifies. Useful for development.

### 7. Cleanup

```bash
# Remove runtime and ECR repo (keep S3 data)
python cleanup.py

# Remove everything including S3 bucket and span data
python cleanup.py --delete-spans
```

## Project Structure

```
├── travel_agent.py       # Strands agent + AgentCore entrypoint + OTel setup
├── s3_span_exporter.py   # Custom OTel SpanExporter → S3
├── simulate_users.py     # Concurrent multi-user simulation script
├── invoke_remote.py      # Single invocation + S3 span check
├── deploy.py             # Deploy to AgentCore Runtime
├── cleanup.py            # Tear down AWS resources
├── test_local.py         # Local end-to-end test
├── requirements.txt      # Python dependencies
└── Dockerfile            # Container image for AgentCore Runtime
```

## S3 Span Format

Each span file is a JSON array of span objects:

```json
[
  {
    "trace_id": "6a96a1bb2725fcdf50a0045f561b30eb",
    "span_id": "a1b2c3d4e5f6a7b8",
    "parent_span_id": null,
    "name": "Agent.agent.invoke_model",
    "kind": "INTERNAL",
    "start_time_unix_nano": 1725340800000000000,
    "end_time_unix_nano": 1725340802500000000,
    "duration_ms": 2500.0,
    "status": { "code": "OK", "description": null },
    "attributes": {
      "gen_ai.request.model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
      "gen_ai.usage.total_tokens": 342,
      "session.id": "user-1-abc123..."
    },
    "events": []
  }
]
```

Spans are partitioned in S3 as: `spans/{session_id}/{trace_id}/{timestamp}-{uuid}.json`

## Configuration

### Model Provider

The demo supports multiple model providers via environment variables. Set these before running `deploy.py`, or pass them in the Dockerfile / container config.

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `MODEL_PROVIDER` | `bedrock`, `openai`, or `litellm` | `bedrock` |
| `MODEL_ID` | Model ID for the chosen provider | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| `MODEL_API_KEY` | API key (required for `openai` / `litellm`) | _(empty)_ |
| `MODEL_API_BASE` | Custom API base URL (optional) | _(empty)_ |

**Examples:**

```bash
# Amazon Bedrock (default, uses IAM role — no API key needed)
export MODEL_PROVIDER=bedrock
export MODEL_ID=us.amazon.nova-lite-v1:0

# OpenAI
export MODEL_PROVIDER=openai
export MODEL_ID=gpt-4o-mini
export MODEL_API_KEY=sk-...

# OpenAI-compatible endpoint (vLLM, Together, Deepseek, etc.)
export MODEL_PROVIDER=openai
export MODEL_ID=deepseek-chat
export MODEL_API_KEY=sk-...
export MODEL_API_BASE=https://api.deepseek.com/v1

# LiteLLM (supports 100+ providers with a unified interface)
export MODEL_PROVIDER=litellm
export MODEL_ID=anthropic/claude-haiku-4-5-20251001
export MODEL_API_KEY=sk-ant-...
```

### S3 Span Export

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `SPAN_BUCKET` | S3 bucket for span export | `agentcore-trace-demo-spans` |
| `SPAN_PREFIX` | S3 key prefix | `spans` |
