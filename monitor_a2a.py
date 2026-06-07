"""
A2A Security Monitor

Analyzes inter-agent communication logs for security anomalies.
Checks for:
- Unauthorized agent targets
- Prompt injection patterns in payloads
- Abnormal payload sizes (potential data exfiltration)
- Latency anomalies (potential resource abuse)
- Error rate spikes
- Unusual call patterns

Usage:
    python monitor_a2a.py                    # Last 1 hour
    python monitor_a2a.py --hours 24         # Last 24 hours
    python monitor_a2a.py --hours 6 --json   # Output as JSON for automation
"""

import boto3
import json
import sys
import argparse
import re
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# ============================================================================
# Configuration
# ============================================================================

REGION = "us-east-1"
ALLOWED_TARGETS = {"specialist", "factchecker"}
MAX_QUERY_LENGTH = 5000       # Flag queries larger than this
MAX_RESPONSE_LENGTH = 50000   # Flag responses larger than this
MAX_LATENCY_MS = 120000       # Flag calls taking longer than 2 minutes
MIN_LATENCY_MS = 100          # Flag suspiciously fast calls (< 100ms)

# Patterns that may indicate prompt injection attempts
INJECTION_PATTERNS = [
    r"ignore.*(?:previous|above|prior).*instructions",
    r"disregard.*(?:system|instructions|prompt)",
    r"you are now",
    r"new instructions",
    r"override.*(?:system|prompt|instructions)",
    r"output.*(?:system prompt|instructions|configuration)",
    r"reveal.*(?:system prompt|secret|api key|credentials)",
    r"(?:print|show|display).*(?:system prompt|instructions)",
    r"act as.*(?:different|new|another)",
    r"forget.*(?:everything|instructions|rules)",
    r"<\/?(?:system|admin|root)>",
    r"```.*(?:system|admin|override)",
]


# ============================================================================
# Log Fetching
# ============================================================================

def get_orchestrator_log_group(logs_client):
    """Find the current orchestrator log group."""
    response = logs_client.describe_log_groups(
        logGroupNamePrefix="/aws/bedrock-agentcore/runtimes/agentcore_multi_agent_OrchestratorAgent"
    )
    groups = response.get("logGroups", [])
    if not groups:
        return None
    # Return the most recently created one (newest runtime)
    return sorted(groups, key=lambda g: g.get("creationTime", 0), reverse=True)[0]["logGroupName"]


def fetch_a2a_logs(logs_client, log_group, hours):
    """Fetch all A2A-related log events."""
    start_time = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)

    events = []
    kwargs = {
        "logGroupName": log_group,
        "startTime": start_time,
        "filterPattern": "A2A_CALL",
    }

    while True:
        response = logs_client.filter_log_events(**kwargs)
        events.extend(response.get("events", []))
        next_token = response.get("nextToken")
        if not next_token:
            break
        kwargs["nextToken"] = next_token

    return events


# ============================================================================
# Parsing
# ============================================================================

def parse_a2a_event(event):
    """Parse a log event into structured A2A call data."""
    msg = event["message"]
    timestamp = event["timestamp"]
    trace_id = ""
    span_id = ""

    # Try OTEL JSON format first
    try:
        obj = json.loads(msg)
        body = obj.get("body", "")
        trace_id = obj.get("traceId", "")
        span_id = obj.get("spanId", "")
        msg = body
    except (json.JSONDecodeError, TypeError):
        # Extract trace from plain text format
        trace_match = re.search(r"trace_id=([a-f0-9]+)", msg)
        if trace_match:
            trace_id = trace_match.group(1)

    # Determine event type
    event_type = None
    if "A2A_CALL_START" in msg:
        event_type = "START"
    elif "A2A_CALL_PAYLOAD" in msg:
        event_type = "PAYLOAD"
    elif "A2A_CALL_END" in msg:
        event_type = "END"
    elif "A2A_CALL_RESPONSE" in msg:
        event_type = "RESPONSE"
    elif "A2A_CALL_ERROR" in msg:
        event_type = "ERROR"

    if not event_type:
        return None

    # Extract fields
    target = ""
    query_length = 0
    response_length = 0
    latency_ms = 0
    payload = ""
    arn = ""

    if "target=" in msg:
        match = re.search(r"target=(\w+)", msg)
        if match:
            target = match.group(1)

    if "arn=" in msg:
        match = re.search(r"arn=(arn:aws:bedrock-agentcore:[^\s|]+)", msg)
        if match:
            arn = match.group(1)

    if "query_length=" in msg:
        match = re.search(r"query_length=(\d+)", msg)
        if match:
            query_length = int(match.group(1))

    if "response_length=" in msg:
        match = re.search(r"response_length=(\d+)", msg)
        if match:
            response_length = int(match.group(1))

    if "latency_ms=" in msg:
        match = re.search(r"latency_ms=(\d+)", msg)
        if match:
            latency_ms = int(match.group(1))

    if "query=" in msg and event_type == "PAYLOAD":
        match = re.search(r"query=(.+?)$", msg)
        if match:
            payload = match.group(1)

    return {
        "type": event_type,
        "timestamp": timestamp,
        "trace_id": trace_id,
        "span_id": span_id,
        "target": target,
        "arn": arn,
        "query_length": query_length,
        "response_length": response_length,
        "latency_ms": latency_ms,
        "payload": payload,
    }


# ============================================================================
# Security Checks
# ============================================================================

def check_unauthorized_targets(calls):
    """Check for calls to unexpected agent targets."""
    findings = []
    for call in calls:
        if call["type"] == "START" and call["target"]:
            if call["target"] not in ALLOWED_TARGETS:
                findings.append({
                    "severity": "HIGH",
                    "check": "unauthorized_target",
                    "message": f"Call to unauthorized target: {call['target']}",
                    "trace_id": call["trace_id"],
                    "timestamp": call["timestamp"],
                })
    return findings


def check_prompt_injection(calls):
    """Check payloads for prompt injection patterns."""
    findings = []
    for call in calls:
        if call["type"] == "PAYLOAD" and call["payload"]:
            for pattern in INJECTION_PATTERNS:
                if re.search(pattern, call["payload"], re.IGNORECASE):
                    findings.append({
                        "severity": "MEDIUM",
                        "check": "prompt_injection",
                        "message": f"Potential injection in payload to {call['target']}: matched '{pattern}'",
                        "trace_id": call["trace_id"],
                        "timestamp": call["timestamp"],
                        "payload_snippet": call["payload"][:100],
                    })
                    break  # One finding per payload
    return findings


def check_payload_sizes(calls):
    """Check for abnormally large payloads (potential data exfiltration)."""
    findings = []
    for call in calls:
        if call["type"] == "START" and call["query_length"] > MAX_QUERY_LENGTH:
            findings.append({
                "severity": "MEDIUM",
                "check": "large_payload",
                "message": f"Large query payload ({call['query_length']} chars) to {call['target']}",
                "trace_id": call["trace_id"],
                "timestamp": call["timestamp"],
            })
        if call["type"] == "END" and call["response_length"] > MAX_RESPONSE_LENGTH:
            findings.append({
                "severity": "LOW",
                "check": "large_response",
                "message": f"Large response ({call['response_length']} chars) from {call['target']}",
                "trace_id": call["trace_id"],
                "timestamp": call["timestamp"],
            })
    return findings


def check_latency_anomalies(calls):
    """Check for unusual latency patterns."""
    findings = []
    for call in calls:
        if call["type"] == "END" and call["latency_ms"] > 0:
            if call["latency_ms"] > MAX_LATENCY_MS:
                findings.append({
                    "severity": "LOW",
                    "check": "high_latency",
                    "message": f"High latency ({call['latency_ms']}ms) calling {call['target']}",
                    "trace_id": call["trace_id"],
                    "timestamp": call["timestamp"],
                })
            elif call["latency_ms"] < MIN_LATENCY_MS:
                findings.append({
                    "severity": "MEDIUM",
                    "check": "suspiciously_fast",
                    "message": f"Suspiciously fast response ({call['latency_ms']}ms) from {call['target']}",
                    "trace_id": call["trace_id"],
                    "timestamp": call["timestamp"],
                })
    return findings


def check_error_rate(calls):
    """Check for elevated error rates."""
    findings = []
    errors = [c for c in calls if c["type"] == "ERROR"]
    total_starts = [c for c in calls if c["type"] == "START"]

    if total_starts and errors:
        error_rate = len(errors) / len(total_starts)
        if error_rate > 0.1:  # >10% error rate
            findings.append({
                "severity": "HIGH",
                "check": "high_error_rate",
                "message": f"Error rate {error_rate:.1%} ({len(errors)}/{len(total_starts)} calls)",
                "trace_id": "",
                "timestamp": 0,
            })

    for error in errors:
        findings.append({
            "severity": "MEDIUM",
            "check": "a2a_error",
            "message": f"A2A call error to {error['target']}",
            "trace_id": error["trace_id"],
            "timestamp": error["timestamp"],
        })

    return findings


def check_call_frequency(calls):
    """Check for unusual call frequency (potential abuse)."""
    findings = []
    # Group by minute
    minute_buckets = defaultdict(int)
    for call in calls:
        if call["type"] == "START":
            minute = call["timestamp"] // 60000
            minute_buckets[minute] += 1

    for minute, count in minute_buckets.items():
        if count > 20:  # More than 20 A2A calls in a single minute
            findings.append({
                "severity": "MEDIUM",
                "check": "high_frequency",
                "message": f"High call frequency: {count} A2A calls in 1 minute",
                "trace_id": "",
                "timestamp": minute * 60000,
            })

    return findings


# ============================================================================
# Reporting
# ============================================================================

def generate_report(calls, findings, hours, output_json=False):
    """Generate security report."""
    # Statistics
    starts = [c for c in calls if c["type"] == "START"]
    ends = [c for c in calls if c["type"] == "END"]
    errors = [c for c in calls if c["type"] == "ERROR"]

    targets_called = defaultdict(int)
    latencies = []
    for call in calls:
        if call["type"] == "START" and call["target"]:
            targets_called[call["target"]] += 1
        if call["type"] == "END" and call["latency_ms"]:
            latencies.append(call["latency_ms"])

    unique_traces = len(set(c["trace_id"] for c in calls if c["trace_id"]))

    stats = {
        "time_window_hours": hours,
        "total_a2a_events": len(calls),
        "total_calls": len(starts),
        "total_errors": len(errors),
        "unique_sessions": unique_traces,
        "targets_called": dict(targets_called),
        "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
        "max_latency_ms": max(latencies) if latencies else 0,
        "min_latency_ms": min(latencies) if latencies else 0,
    }

    severity_counts = defaultdict(int)
    for f in findings:
        severity_counts[f["severity"]] += 1

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "statistics": stats,
        "findings_summary": {
            "total": len(findings),
            "high": severity_counts["HIGH"],
            "medium": severity_counts["MEDIUM"],
            "low": severity_counts["LOW"],
        },
        "findings": findings,
    }

    if output_json:
        print(json.dumps(report, indent=2, default=str))
        return report

    # Human-readable output
    print("\n" + "=" * 70)
    print("  A2A SECURITY MONITOR REPORT")
    print("=" * 70)

    print(f"\n  Time window: Last {hours} hour(s)")
    print(f"  Generated:   {report['generated_at']}")

    print(f"\n{'─' * 70}")
    print("  STATISTICS")
    print(f"{'─' * 70}")
    print(f"  Total A2A calls:    {stats['total_calls']}")
    print(f"  Total errors:       {stats['total_errors']}")
    print(f"  Unique sessions:    {stats['unique_sessions']}")
    print(f"  Agents called:      {dict(targets_called)}")
    if latencies:
        print(f"  Latency (avg):      {stats['avg_latency_ms']}ms")
        print(f"  Latency (max):      {stats['max_latency_ms']}ms")
        print(f"  Latency (min):      {stats['min_latency_ms']}ms")

    print(f"\n{'─' * 70}")
    print("  SECURITY FINDINGS")
    print(f"{'─' * 70}")

    if not findings:
        print("\n  [OK] No security issues detected.")
    else:
        print(f"\n  Total: {len(findings)}  |  "
              f"HIGH: {severity_counts['HIGH']}  |  "
              f"MEDIUM: {severity_counts['MEDIUM']}  |  "
              f"LOW: {severity_counts['LOW']}")
        print()
        for f in sorted(findings, key=lambda x: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[x["severity"]]):
            icon = {"HIGH": "[!!!]", "MEDIUM": "[!!]", "LOW": "[!]"}[f["severity"]]
            ts = ""
            if f["timestamp"]:
                ts = datetime.fromtimestamp(f["timestamp"] / 1000, tz=timezone.utc).strftime("%H:%M:%S")
            print(f"  {icon} [{f['severity']}] {f['message']}")
            if ts:
                print(f"       Time: {ts}  Trace: {f['trace_id'][:16]}...")
            if f.get("payload_snippet"):
                print(f"       Payload: {f['payload_snippet']}")
            print()

    print("=" * 70)
    return report


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="A2A Security Monitor")
    parser.add_argument("--hours", type=int, default=1, help="Hours to look back (default: 1)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    logs_client = boto3.client("logs", region_name=REGION)

    # Find log group
    log_group = get_orchestrator_log_group(logs_client)
    if not log_group:
        print("ERROR: Could not find Orchestrator log group")
        sys.exit(1)

    # Fetch logs
    events = fetch_a2a_logs(logs_client, log_group, args.hours)
    if not events:
        if args.json:
            print(json.dumps({"findings": [], "statistics": {"total_calls": 0}}))
        else:
            print(f"\nNo A2A calls found in the last {args.hours} hour(s).")
            print(f"Log group: {log_group}")
        sys.exit(0)

    # Parse events (deduplicate — each event appears in both plain and OTEL format)
    seen = set()
    calls = []
    for event in events:
        parsed = parse_a2a_event(event)
        if parsed:
            # Deduplicate by trace+type+target+timestamp
            key = f"{parsed['trace_id']}:{parsed['type']}:{parsed['target']}:{parsed['timestamp'] // 1000}"
            if key not in seen:
                seen.add(key)
                calls.append(parsed)

    # Run security checks
    findings = []
    findings.extend(check_unauthorized_targets(calls))
    findings.extend(check_prompt_injection(calls))
    findings.extend(check_payload_sizes(calls))
    findings.extend(check_latency_anomalies(calls))
    findings.extend(check_error_rate(calls))
    findings.extend(check_call_frequency(calls))

    # Report
    generate_report(calls, findings, args.hours, output_json=args.json)


if __name__ == "__main__":
    main()
