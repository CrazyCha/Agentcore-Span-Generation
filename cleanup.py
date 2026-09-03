"""Clean up AgentCore Runtime, ECR repo, and optionally the S3 bucket."""

import json
import sys

import boto3

with open("deploy-output.json") as f:
    deploy = json.load(f)

region = deploy["region"]
agent_id = deploy["agent_id"]
ecr_uri = deploy["ecr_uri"]
span_bucket = deploy["span_bucket"]

print(f"Cleaning up agent: {deploy['agent_name']} ({agent_id})")

# Delete AgentCore Runtime
print("\n1. Deleting AgentCore Runtime...")
agentcore_control = boto3.client("bedrock-agentcore-control", region_name=region)
try:
    agentcore_control.delete_agent_runtime(agentRuntimeId=agent_id)
    print(f"   Deleted runtime: {agent_id}")
except Exception as e:
    print(f"   Failed: {e}")

# Delete ECR repository
print("\n2. Deleting ECR repository...")
ecr = boto3.client("ecr", region_name=region)
repo_name = ecr_uri.split("/")[1] if "/" in ecr_uri else ecr_uri
try:
    ecr.delete_repository(repositoryName=repo_name, force=True)
    print(f"   Deleted ECR repo: {repo_name}")
except Exception as e:
    print(f"   Failed: {e}")

# Optionally clean S3
if "--delete-spans" in sys.argv:
    print(f"\n3. Deleting span files from s3://{span_bucket}/spans/...")
    s3 = boto3.client("s3", region_name=region)
    paginator = s3.get_paginator("list_objects_v2")
    deleted = 0
    for page in paginator.paginate(Bucket=span_bucket, Prefix="spans/"):
        objects = page.get("Contents", [])
        if objects:
            s3.delete_objects(
                Bucket=span_bucket,
                Delete={"Objects": [{"Key": o["Key"]} for o in objects]},
            )
            deleted += len(objects)
    print(f"   Deleted {deleted} span file(s)")

    print(f"\n4. Deleting S3 bucket: {span_bucket}...")
    try:
        s3.delete_bucket(Bucket=span_bucket)
        print(f"   Deleted bucket: {span_bucket}")
    except Exception as e:
        print(f"   Failed: {e}")
else:
    print(f"\nS3 bucket '{span_bucket}' preserved. Use --delete-spans to remove.")

print("\nCleanup complete.")
