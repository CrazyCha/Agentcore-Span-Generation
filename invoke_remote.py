"""Invoke the deployed AgentCore Runtime agent and verify spans in S3."""

import json
import time
import sys

import boto3

# ── Load deployment info ─────────────────────────────────────────────────────
with open("deploy-output.json") as f:
    deploy = json.load(f)

agent_arn = deploy["agent_arn"]
region = deploy["region"]
span_bucket = deploy["span_bucket"]

print(f"Agent ARN: {agent_arn}")
print(f"Span bucket: {span_bucket}")

# ── Invoke agent ─────────────────────────────────────────────────────────────
prompt = sys.argv[1] if len(sys.argv) > 1 else "Plan a 3-night trip to Paris with flights, hotels, and weather."
print(f"\nPrompt: {prompt}")
print("-" * 60)

agentcore = boto3.client("bedrock-agentcore", region_name=region)

response = agentcore.invoke_agent_runtime(
    agentRuntimeArn=agent_arn,
    qualifier="DEFAULT",
    payload=json.dumps({"prompt": prompt}),
)

content_type = response.get("contentType", "")
if "text/event-stream" in content_type:
    content = []
    for line in response["response"].iter_lines(chunk_size=1):
        if line:
            line = line.decode("utf-8")
            if line.startswith("data: "):
                line = line[6:]
            content.append(line)
            print(line, end="", flush=True)
    print()
else:
    raw_chunks = []
    for event in response.get("response", []):
        if isinstance(event, bytes):
            raw_chunks.append(event.decode("utf-8"))
        elif isinstance(event, dict):
            raw_chunks.append(json.dumps(event))
        else:
            raw_chunks.append(str(event))
    result = "".join(raw_chunks)
    print(result)

# ── Wait and check S3 for spans ─────────────────────────────────────────────
print("\n" + "-" * 60)
print("Waiting 10s for spans to flush to S3...")
time.sleep(10)

s3 = boto3.client("s3", region_name=region)
resp = s3.list_objects_v2(Bucket=span_bucket, Prefix="spans/", MaxKeys=20)
objects = resp.get("Contents", [])

recent = sorted(objects, key=lambda o: o["LastModified"], reverse=True)[:5]
print(f"\nMost recent span files in S3 ({len(objects)} total):")
for obj in recent:
    print(f"  {obj['Key']}  ({obj['Size']} bytes)  {obj['LastModified']}")

if recent:
    body = s3.get_object(Bucket=span_bucket, Key=recent[0]["Key"])["Body"].read()
    spans = json.loads(body)
    print(f"\nNewest file has {len(spans)} span(s):")
    for sp in spans:
        dur = f"{sp['duration_ms']:.0f}ms" if sp.get("duration_ms") else "?"
        attrs = sp.get("attributes", {})
        model = attrs.get("gen_ai.request.model", "")
        tokens = attrs.get("gen_ai.usage.total_tokens", "")
        extra = f" | model={model} tokens={tokens}" if model else ""
        print(f"  [{dur}] {sp['name']}{extra}")
