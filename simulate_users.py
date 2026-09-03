"""Simulate concurrent ToC users, each running a multi-turn conversation.

Usage:
    python simulate_users.py --users 5 --turns 3          # 5 concurrent users, 3 turns each
    python simulate_users.py --users 10 --turns 4 --delay 2  # 10 users, 4 turns, 2s between turns
"""

import argparse
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import boto3

# ── Pre-defined conversation scripts ────────────────────────────────────────
# Each script is a list of prompts forming a coherent multi-turn conversation.
# Scripts are assigned round-robin to users.
CONVERSATION_SCRIPTS = [
    [
        "I want to plan a trip to Tokyo. Search for available flights.",
        "Find me hotels in Tokyo for 3 nights. I prefer something affordable.",
        "What's the weather like in Tokyo right now?",
        "Give me a complete budget breakdown for the cheapest flight and hotel option to Tokyo.",
    ],
    [
        "Check the weather in Paris for me.",
        "Looks good! Search for flights to Paris.",
        "Now find hotels in Paris for 5 nights.",
        "Summarize the best value trip to Paris with flight, hotel, and total cost.",
    ],
    [
        "I'm comparing destinations. Check weather in Tokyo and Paris.",
        "Search flights to both Tokyo and Paris and compare prices.",
        "Find hotels in Tokyo for 4 nights.",
        "Find hotels in Paris for 4 nights and compare with Tokyo options.",
    ],
    [
        "Search for flights to Sydney. I want the best deal.",
        "What's the weather in Sydney?",
        "Find me hotels in Sydney for 7 nights.",
        "Plan a complete week-long Sydney trip with the cheapest options.",
    ],
    [
        "I have a budget of $2000 for a 3-night trip. What destinations can I afford?",
        "Search flights to Paris and check available hotels for 3 nights.",
        "Now check Tokyo - flights and hotels for 3 nights.",
        "Compare Paris vs Tokyo for my $2000 budget and recommend one.",
    ],
    [
        "Check the weather in Sydney, Tokyo, and Paris.",
        "Search for flights to Sydney.",
        "Find hotels in Sydney for 2 nights.",
        "I changed my mind - search flights to Tokyo instead and find hotels for 2 nights.",
    ],
]


def invoke_agent(client, agent_arn, prompt, session_id):
    """Invoke the agent with a given prompt and session ID."""
    response = client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        qualifier="DEFAULT",
        runtimeSessionId=session_id,
        payload=json.dumps({"prompt": prompt}),
    )

    content_type = response.get("contentType", "")
    if "text/event-stream" in content_type:
        chunks = []
        for line in response["response"].iter_lines(chunk_size=1):
            if line:
                line = line.decode("utf-8")
                if line.startswith("data: "):
                    line = line[6:]
                chunks.append(line)
        return "".join(chunks)
    else:
        raw_chunks = []
        for event in response.get("response", []):
            if isinstance(event, bytes):
                raw_chunks.append(event.decode("utf-8"))
            elif isinstance(event, dict):
                raw_chunks.append(json.dumps(event))
            else:
                raw_chunks.append(str(event))
        return "".join(raw_chunks)


def run_user_session(user_id, agent_arn, region, prompts, delay_between_turns):
    """Run one user's complete multi-turn conversation."""
    client = boto3.client("bedrock-agentcore", region_name=region)
    session_id = f"user-{user_id}-{uuid.uuid4().hex}"
    results = []

    print(f"  [User {user_id}] Session {session_id} started ({len(prompts)} turns)")

    for turn, prompt in enumerate(prompts, 1):
        t0 = time.time()
        try:
            reply = invoke_agent(client, agent_arn, prompt, session_id)
            elapsed = time.time() - t0
            results.append({
                "turn": turn,
                "prompt": prompt,
                "reply_length": len(reply),
                "latency_s": round(elapsed, 2),
                "status": "ok",
            })
            print(f"  [User {user_id}] Turn {turn}/{len(prompts)} done ({elapsed:.1f}s)")
        except Exception as e:
            elapsed = time.time() - t0
            results.append({
                "turn": turn,
                "prompt": prompt,
                "error": str(e),
                "latency_s": round(elapsed, 2),
                "status": "error",
            })
            print(f"  [User {user_id}] Turn {turn}/{len(prompts)} FAILED: {e}")

        if turn < len(prompts) and delay_between_turns > 0:
            time.sleep(delay_between_turns)

    return {"user_id": user_id, "session_id": session_id, "turns": results}


def check_s3_spans(region, bucket, wait_seconds=15):
    """Wait for span flush then report S3 stats."""
    print(f"\nWaiting {wait_seconds}s for spans to flush to S3...")
    time.sleep(wait_seconds)

    s3 = boto3.client("s3", region_name=region)
    resp = s3.list_objects_v2(Bucket=bucket, Prefix="spans/", MaxKeys=1000)
    objects = resp.get("Contents", [])

    sessions = set()
    total_spans = 0
    for obj in objects:
        parts = obj["Key"].split("/")
        if len(parts) >= 3:
            sessions.add(parts[1])
        try:
            body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
            spans = json.loads(body)
            total_spans += len(spans)
        except Exception:
            pass

    return {
        "total_files": len(objects),
        "total_spans": total_spans,
        "unique_sessions": len(sessions),
        "session_ids": sorted(sessions),
    }


def main():
    parser = argparse.ArgumentParser(description="Simulate concurrent ToC users on AgentCore Runtime")
    parser.add_argument("--users", type=int, default=3, help="Number of concurrent user sessions (default: 3)")
    parser.add_argument("--turns", type=int, default=None,
                        help="Max turns per user (default: use full conversation script)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Seconds between turns within a session (default: 1.0)")
    parser.add_argument("--skip-s3-check", action="store_true", help="Skip S3 span verification")
    args = parser.parse_args()

    with open("deploy-output.json") as f:
        deploy = json.load(f)
    agent_arn = deploy["agent_arn"]
    region = deploy["region"]
    span_bucket = deploy["span_bucket"]

    num_users = args.users
    max_turns = args.turns

    print("=" * 70)
    print(f"  AgentCore Concurrent Session Demo")
    print(f"  Time:       {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Agent ARN:  {agent_arn}")
    print(f"  Users:      {num_users}")
    print(f"  Turns/user: {max_turns or 'full script'}")
    print(f"  Turn delay: {args.delay}s")
    print("=" * 70)

    # Assign conversation scripts round-robin, optionally truncate to --turns
    user_prompts = {}
    for i in range(num_users):
        script = CONVERSATION_SCRIPTS[i % len(CONVERSATION_SCRIPTS)]
        if max_turns:
            script = script[:max_turns]
        user_prompts[i + 1] = script

    total_invocations = sum(len(p) for p in user_prompts.values())
    print(f"\nLaunching {num_users} users × ~{len(user_prompts[1])} turns = {total_invocations} total invocations\n")

    t_start = time.time()
    all_results = []

    with ThreadPoolExecutor(max_workers=num_users) as pool:
        futures = {
            pool.submit(
                run_user_session, uid, agent_arn, region, prompts, args.delay
            ): uid
            for uid, prompts in user_prompts.items()
        }
        for future in as_completed(futures):
            uid = futures[future]
            try:
                result = future.result()
                all_results.append(result)
            except Exception as e:
                print(f"  [User {uid}] Session crashed: {e}")

    total_time = time.time() - t_start

    # ── Summary ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  Results Summary")
    print("=" * 70)

    ok_count = sum(1 for r in all_results for t in r["turns"] if t["status"] == "ok")
    err_count = sum(1 for r in all_results for t in r["turns"] if t["status"] == "error")
    latencies = [t["latency_s"] for r in all_results for t in r["turns"] if t["status"] == "ok"]

    print(f"  Total time:       {total_time:.1f}s")
    print(f"  Invocations:      {ok_count} ok / {err_count} error / {total_invocations} total")
    if latencies:
        print(f"  Latency (avg):    {sum(latencies) / len(latencies):.1f}s")
        print(f"  Latency (min):    {min(latencies):.1f}s")
        print(f"  Latency (max):    {max(latencies):.1f}s")

    print(f"\n  Per-user breakdown:")
    for r in sorted(all_results, key=lambda x: x["user_id"]):
        turns_ok = sum(1 for t in r["turns"] if t["status"] == "ok")
        avg_lat = sum(t["latency_s"] for t in r["turns"] if t["status"] == "ok") / max(turns_ok, 1)
        print(f"    User {r['user_id']:>2} | session={r['session_id']} | "
              f"{turns_ok}/{len(r['turns'])} ok | avg {avg_lat:.1f}s")

    # ── S3 span check ──────────────────────────────────────────────────────
    if not args.skip_s3_check:
        print("\n" + "-" * 70)
        s3_stats = check_s3_spans(region, span_bucket)
        print(f"\n  S3 Span Summary:")
        print(f"    Span files:      {s3_stats['total_files']}")
        print(f"    Total spans:     {s3_stats['total_spans']}")
        print(f"    Unique sessions: {s3_stats['unique_sessions']}")
        if s3_stats["unique_sessions"] <= 20:
            for sid in s3_stats["session_ids"]:
                print(f"      - {sid}")

    print("\n" + "=" * 70)
    print("  Demo complete!")
    print("=" * 70)


if __name__ == "__main__":
    main()
