"""Deploy travel agent to AgentCore Runtime with S3 span export."""

import json
import os
import time

import boto3
from boto3.session import Session
from bedrock_agentcore_starter_toolkit import Runtime

boto_session = Session()
region = boto_session.region_name
account_id = boto3.client("sts").get_caller_identity()["Account"]

AGENT_NAME = "travel_agent_trace_s3"
SPAN_BUCKET = os.environ.get("SPAN_BUCKET", f"agentcore-trace-spans-{account_id}-{region}")

BEDROCK_API_KEY = os.environ.get("BEDROCK_API_KEY", "")
if not BEDROCK_API_KEY:
    print("ERROR: BEDROCK_API_KEY environment variable is required.")
    print("  Create one in the Amazon Bedrock console → API keys,")
    print("  then run: export BEDROCK_API_KEY=ABSK...")
    exit(1)

# ── 1. Ensure S3 bucket exists ───────────────────────────────────────────────
s3 = boto3.client("s3", region_name=region)
try:
    s3.head_bucket(Bucket=SPAN_BUCKET)
    print(f"S3 bucket exists: {SPAN_BUCKET}")
except Exception:
    print(f"Creating S3 bucket: {SPAN_BUCKET}")
    create_args = {"Bucket": SPAN_BUCKET}
    if region and region != "us-east-1":
        create_args["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**create_args)
    print(f"  Created: s3://{SPAN_BUCKET}")

# ── 2. Ensure execution role has S3 write permission ─────────────────────────
iam = boto3.client("iam")
role_name = f"{AGENT_NAME}-execution-role"
s3_policy_name = f"{AGENT_NAME}-s3-span-write"

s3_policy_doc = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["s3:PutObject"],
            "Resource": [f"arn:aws:s3:::{SPAN_BUCKET}/spans/*"],
        }
    ],
}

# ── 2. Configure AgentCore Runtime ───────────────────────────────────────────
print("Configuring AgentCore Runtime...")
agentcore_runtime = Runtime()

response = agentcore_runtime.configure(
    entrypoint="travel_agent.py",
    auto_create_execution_role=True,
    auto_create_ecr=True,
    requirements_file="requirements.txt",
    region=region,
    agent_name=AGENT_NAME,
    memory_mode="NO_MEMORY",
)
print(f"Configure result: {response}")

# ── 3. Launch ────────────────────────────────────────────────────────────────
print("\nLaunching agent to AgentCore Runtime (this takes a few minutes)...")
launch_result = agentcore_runtime.launch(env_vars={
    "BEDROCK_API_KEY": BEDROCK_API_KEY,
    "MODEL_VARIANT": os.environ.get("MODEL_VARIANT", "terra"),
    "BEDROCK_REGION": region,
    "SPAN_BUCKET": SPAN_BUCKET,
})
print(f"Agent ARN: {launch_result.agent_arn}")
print(f"Agent ID: {launch_result.agent_id}")
print(f"ECR URI: {launch_result.ecr_uri}")

# ── 4. Wait for READY ───────────────────────────────────────────────────────
print("\nWaiting for runtime to become READY...")
end_statuses = {"READY", "CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"}
while True:
    status_response = agentcore_runtime.status()
    status = status_response.endpoint["status"]
    print(f"  Status: {status}")
    if status in end_statuses:
        break
    time.sleep(15)

if status != "READY":
    print(f"\nDeployment failed with status: {status}")
    exit(1)

# ── 5. Attach S3 write policy to the execution role ─────────────────────────
print(f"\nAttaching S3 span write policy to execution role...")
try:
    status_resp = agentcore_runtime.status()
    role_arn = status_resp.agent.get("roleArn", "")
    runtime_role = role_arn.split("/")[-1] if role_arn else ""

    if runtime_role:
        iam.put_role_policy(
            RoleName=runtime_role,
            PolicyName=s3_policy_name,
            PolicyDocument=json.dumps(s3_policy_doc),
        )
        print(f"  Attached policy '{s3_policy_name}' to role '{runtime_role}'")
    else:
        print(f"  WARNING: Could not find execution role. You may need to manually attach S3 permissions.")
        print(f"  Policy document: {json.dumps(s3_policy_doc, indent=2)}")
except Exception as e:
    print(f"  WARNING: Failed to attach S3 policy: {e}")
    print(f"  You may need to manually add S3:PutObject permission for bucket '{SPAN_BUCKET}'")

# ── 6. Save deployment info ──────────────────────────────────────────────────
deploy_info = {
    "agent_name": AGENT_NAME,
    "agent_id": launch_result.agent_id,
    "agent_arn": launch_result.agent_arn,
    "ecr_uri": launch_result.ecr_uri,
    "region": region,
    "span_bucket": SPAN_BUCKET,
    "status": status,
}
with open("deploy-output.json", "w") as f:
    json.dump(deploy_info, f, indent=2)
print(f"\nDeployment info saved to deploy-output.json")

print("\n" + "=" * 60)
print("  Deployment complete!")
print(f"  Agent ARN: {launch_result.agent_arn}")
print(f"  Status: {status}")
print("=" * 60)
