"""Fetch and display A2A call logs from the Orchestrator agent.

Usage:
    python check_a2a.py              # Last 7 days (default)
    python check_a2a.py --hours 1    # Last 1 hour
    python check_a2a.py --hours 168  # Last 7 days
    python check_a2a.py --days 30    # Last 30 days
"""
import boto3
import json
import sys
import argparse
from datetime import datetime, timedelta, timezone

REGION = "us-east-1"
LOG_GROUP = "/aws/bedrock-agentcore/runtimes/agentcore_multi_agent_OrchestratorAgent-kbGAc9F1OQ-DEFAULT"

parser = argparse.ArgumentParser(description="Fetch A2A call logs")
parser.add_argument("--hours", type=int, default=None, help="Hours to look back")
parser.add_argument("--days", type=int, default=None, help="Days to look back")
args = parser.parse_args()

if args.days:
    lookback = timedelta(days=args.days)
    label = f"{args.days} day(s)"
elif args.hours:
    lookback = timedelta(hours=args.hours)
    label = f"{args.hours} hour(s)"
else:
    lookback = timedelta(days=7)
    label = "7 days"

client = boto3.client("logs", region_name=REGION)

start_time = int((datetime.now(timezone.utc) - lookback).timestamp() * 1000)

print(f"Fetching A2A calls from last {label}...\n")

response = client.filter_log_events(
    logGroupName=LOG_GROUP,
    startTime=start_time,
    filterPattern="A2A_CALL",
)

a2a_calls = []
events = response.get("events", [])

# Paginate through all results
while True:
    for event in events:
        msg = event["message"]
        try:
            obj = json.loads(msg)
            body = obj.get("body", "")
            trace_id = obj.get("traceId", "")
            span_id = obj.get("spanId", "")
            timestamp = obj.get("timeUnixNano", 0)
            if "A2A_CALL" in str(body):
                a2a_calls.append({
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "body": body,
                    "timestamp": timestamp,
                })
        except json.JSONDecodeError:
            if "A2A_CALL" in msg:
                a2a_calls.append({"body": msg.strip(), "trace_id": "", "span_id": "", "timestamp": 0})

    next_token = response.get("nextToken")
    if not next_token:
        break
    response = client.filter_log_events(
        logGroupName=LOG_GROUP,
        startTime=start_time,
        filterPattern="A2A_CALL",
        nextToken=next_token,
    )
    events = response.get("events", [])

if not a2a_calls:
    print(f"No A2A calls found in the last {label}.")
    print(f"Log group: {LOG_GROUP}")
    sys.exit(0)

print(f"Found {len(a2a_calls)} A2A log entries:\n")
print("=" * 80)

for call in a2a_calls:
    trace = call["trace_id"][:16] + "..." if call["trace_id"] else "N/A"
    print(f"  Trace: {trace}")
    print(f"  Body:  {call['body'][:300]}")
    print("-" * 80)

# Security analysis
print("\n\n" + "=" * 80)
print("SECURITY ANALYSIS OF A2A CALLS")
print("=" * 80)

# Group by trace
traces = {}
for call in a2a_calls:
    tid = call["trace_id"] or "unknown"
    if tid not in traces:
        traces[tid] = []
    traces[tid].append(call)

print(f"\nUnique traces (sessions): {len(traces)}")

# Check for unexpected targets
targets = set()
for call in a2a_calls:
    body = call["body"]
    if "target=" in body:
        # Extract target agent name
        parts = body.split("target=")
        if len(parts) > 1:
            target = parts[1].split(" ")[0].split("|")[0].strip()
            targets.add(target)

print(f"Agents called: {targets}")

# Check for errors
errors = [c for c in a2a_calls if "ERROR" in c["body"]]
print(f"A2A errors: {len(errors)}")

# Check payload sizes
payloads = [c for c in a2a_calls if "PAYLOAD" in c["body"]]
print(f"Payloads logged: {len(payloads)}")

# Flag potential concerns
print("\n--- Security Flags ---")
for call in a2a_calls:
    body = call["body"]
    # Check for large payloads (potential data exfil)
    if "query_length=" in body:
        length = int(body.split("query_length=")[1].split("|")[0].split()[0])
        if length > 1000:
            print(f"  [WARN] Large payload ({length} chars) in trace {call['trace_id'][:12]}")
    # Check for unexpected agents
    if "target=" in body:
        target = body.split("target=")[1].split(" ")[0].split("|")[0].strip()
        if target not in ("specialist", "factchecker"):
            print(f"  [ALERT] Unexpected target agent: {target}")
    # Check for errors
    if "ERROR" in body:
        print(f"  [ERROR] {body[:150]}")

print("\nDone.")
