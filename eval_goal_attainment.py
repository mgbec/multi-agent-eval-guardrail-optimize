"""
Goal Attainment Evaluation for Multi-Agent System

Uses AgentCore's built-in evaluators to assess whether the multi-agent
system actually achieved what the user asked for.

Evaluators used:
- Builtin.Helpfulness - Did the response help the user?
- Builtin.GoalSuccessRate - Did the agent complete the user's goal?
- Builtin.ToolSelectionAccuracy - Were the right tools (agents) selected?

Usage:
    python eval_goal_attainment.py                          # Evaluate most recent session
    python eval_goal_attainment.py --session-id <id>        # Evaluate specific session
    python eval_goal_attainment.py --invoke "Your question" # Invoke then evaluate
    python eval_goal_attainment.py --all-recent             # Evaluate last 5 sessions

Prerequisites:
    - CloudWatch Transaction Search enabled
    - Agent must have been invoked (traces must exist in CloudWatch)
    - pip install bedrock-agentcore-starter-toolkit  (optional, for simplified API)
"""

import boto3
import json
import sys
import io
import time
import argparse
import subprocess
from datetime import datetime, timedelta, timezone
from botocore.config import Config

# Fix Unicode on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

REGION = "us-east-1"
EVALUATORS = [
    "Builtin.Helpfulness",
    "Builtin.GoalSuccessRate",
    "Builtin.ToolSelectionAccuracy",
]


# ============================================================================
# Helpers
# ============================================================================

def get_orchestrator_arn():
    """Get ARN from terraform output."""
    try:
        result = subprocess.run(
            ["terraform", "output", "-raw", "orchestrator_runtime_arn"],
            capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def get_agent_id_from_arn(arn):
    """Extract agent ID from ARN."""
    # arn:aws:bedrock-agentcore:region:account:runtime/agent_id
    return arn.split("/")[-1]


def find_orchestrator_log_group(logs_client):
    """Auto-discover the most recent Orchestrator log group."""
    response = logs_client.describe_log_groups(
        logGroupNamePrefix="/aws/bedrock-agentcore/runtimes/agentcore_multi_agent_OrchestratorAgent"
    )
    groups = response.get("logGroups", [])
    if not groups:
        return None
    return sorted(groups, key=lambda g: g.get("creationTime", 0), reverse=True)[0]["logGroupName"]


# ============================================================================
# Span Collection
# ============================================================================

def query_logs(logs_client, log_group_name, query_string, minutes_back=120):
    """Run a CloudWatch Logs Insights query and return results."""
    start_time = datetime.now(timezone.utc) - timedelta(minutes=minutes_back)
    end_time = datetime.now(timezone.utc)

    query_id = logs_client.start_query(
        logGroupName=log_group_name,
        startTime=int(start_time.timestamp()),
        endTime=int(end_time.timestamp()),
        queryString=query_string
    )["queryId"]

    while True:
        result = logs_client.get_query_results(queryId=query_id)
        if result["status"] in ["Complete", "Failed"]:
            break
        time.sleep(1)

    if result["status"] == "Failed":
        return []
    return result["results"]


def get_recent_trace_ids(logs_client, log_group, limit=5, minutes_back=120):
    """Get recent trace IDs from the Orchestrator's log group.
    Trace IDs are shared across all agents in a single request via OTEL propagation."""
    import re

    # Find trace IDs from the orchestrator's OTEL logs
    query = f"""fields @message
    | filter @message like /traceId/
    | sort @timestamp desc
    | limit 200"""

    results = query_logs(logs_client, log_group, query, minutes_back=minutes_back)

    trace_ids = set()
    for row in results:
        for field in row:
            if field["field"] == "@message":
                # Extract traceId from JSON spans
                matches = re.findall(r'"traceId"\s*:\s*"([a-f0-9]{32})"', field["value"])
                for m in matches:
                    if m != "0" * 32:  # Skip empty trace IDs
                        trace_ids.add(m)

    return list(trace_ids)[:limit]


def collect_spans_by_trace_id(logs_client, log_group, trace_id, minutes_back=120):
    """Collect ALL spans AND log events for a trace ID across all log groups.
    This captures the full multi-agent interaction including conversation content."""
    import re

    all_spans = []

    # Search aws/spans (platform spans + ADOT spans from all agents)
    aws_spans_query = f"""fields @message
    | filter @message like "{trace_id}"
    | sort @timestamp asc
    | limit 1000"""

    aws_results = query_logs(logs_client, "aws/spans", aws_spans_query, minutes_back=minutes_back)
    for row in aws_results:
        for field in row:
            if field["field"] == "@message" and field["value"].strip().startswith("{"):
                try:
                    all_spans.append(json.loads(field["value"]))
                except json.JSONDecodeError:
                    pass

    # Search the orchestrator's runtime log group (contains Strands log events with conversation content)
    runtime_query = f"""fields @message
    | filter @message like "{trace_id}"
    | sort @timestamp asc
    | limit 1000"""

    runtime_results = query_logs(logs_client, log_group, runtime_query, minutes_back=minutes_back)
    for row in runtime_results:
        for field in row:
            if field["field"] == "@message" and field["value"].strip().startswith("{"):
                try:
                    all_spans.append(json.loads(field["value"]))
                except json.JSONDecodeError:
                    pass

    # Also search ALL agent runtime log groups for this trace
    # (downstream agents emit log events with conversation content too)
    agent_log_prefixes = [
        "/aws/bedrock-agentcore/runtimes/agentcore_multi_agent_SpecialistAgent",
        "/aws/bedrock-agentcore/runtimes/agentcore_multi_agent_FactCheckerAgent",
        "/aws/bedrock-agentcore/runtimes/agentcore_multi_agent_CriticAgent",
    ]

    for prefix in agent_log_prefixes:
        try:
            response = logs_client.describe_log_groups(logGroupNamePrefix=prefix)
            groups = response.get("logGroups", [])
            if groups:
                latest_group = sorted(groups, key=lambda g: g.get("creationTime", 0), reverse=True)[0]["logGroupName"]
                agent_results = query_logs(logs_client, latest_group, runtime_query, minutes_back=minutes_back)
                for row in agent_results:
                    for field in row:
                        if field["field"] == "@message" and field["value"].strip().startswith("{"):
                            try:
                                all_spans.append(json.loads(field["value"]))
                            except json.JSONDecodeError:
                                pass
        except Exception:
            pass  # Skip if log group doesn't exist

    # Deduplicate by spanId (but keep log events which may share spanId with their parent span)
    seen = set()
    unique_spans = []
    for span in all_spans:
        # Create a unique key from spanId + body content (to keep both spans and their log events)
        span_id = span.get("spanId", "")
        has_body = "body" in span and isinstance(span.get("body"), dict)
        key = f"{span_id}:{has_body}:{span.get('timeUnixNano', '')}"
        if key not in seen:
            seen.add(key)
            unique_spans.append(span)

    return unique_spans


# ============================================================================
# Invocation
# ============================================================================

def invoke_agent(agentcore_client, arn, prompt, session_id=None):
    """Invoke the orchestrator and return the session ID and response."""
    import uuid
    if not session_id:
        session_id = f"eval-{uuid.uuid4()}"

    print(f"  Invoking with session: {session_id}")
    print(f"  Prompt: {prompt[:80]}...")

    response = agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=session_id,
        qualifier="DEFAULT",
        payload=json.dumps({"prompt": prompt}),
    )

    body = response["response"].read().decode("utf-8")
    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        result = {"raw": body}

    return session_id, result


# ============================================================================
# Evaluation
# ============================================================================

def run_evaluation(agentcore_client, evaluator_id, session_spans, trace_ids=None):
    """Run a single evaluator against session spans."""
    kwargs = {
        "evaluatorId": evaluator_id,
        "evaluationInput": {"sessionSpans": session_spans},
    }

    # Add trace targeting for trace-level evaluators
    if trace_ids and evaluator_id in ("Builtin.Helpfulness", "Builtin.GoalSuccessRate"):
        kwargs["evaluationTarget"] = {"traceIds": trace_ids}

    try:
        response = agentcore_client.evaluate(**kwargs)
        return response.get("evaluationResults", [])
    except Exception as e:
        return [{"evaluatorId": evaluator_id, "errorMessage": str(e), "errorCode": "CLIENT_ERROR"}]


def evaluate_session(agentcore_client, logs_client, log_group, trace_id, minutes_back=120):
    """Run all evaluators against a trace and return results."""
    print(f"\n  Collecting spans for trace: {trace_id[:16]}...")

    all_spans = collect_spans_by_trace_id(logs_client, log_group, trace_id, minutes_back=minutes_back)
    if not all_spans:
        return {"trace_id": trace_id, "error": "No spans found", "results": []}

    print(f"  Found {len(all_spans)} total spans across all agents")

    # The Evaluate API requires spans from a single session.
    # Group by session ID and use the Orchestrator's session (which has the user's question + final answer)
    import re
    sessions = {}
    for span in all_spans:
        # Extract session.id from span
        sid = ""
        attrs = span.get("attributes", {})
        if isinstance(attrs, dict):
            sid = attrs.get("session.id", "")
        if not sid:
            # Try to find it in the JSON string representation
            span_str = json.dumps(span)
            match = re.search(r'"session\.id"\s*:\s*"([^"]+)"', span_str)
            if match:
                sid = match.group(1)
        if not sid:
            sid = "unknown"

        if sid not in sessions:
            sessions[sid] = []
        sessions[sid].append(span)

    print(f"  Sessions found: {len(sessions)}")
    for sid, spans in sessions.items():
        # Identify which agent this session belongs to
        agent_hint = "unknown"
        for s in spans:
            name = s.get("name", "")
            resource = s.get("resource", {}).get("attributes", {})
            svc = resource.get("service.name", "")
            if "orchestrator" in svc.lower() or "Orchestrator" in name:
                agent_hint = "orchestrator"
                break
            elif "specialist" in svc.lower():
                agent_hint = "specialist"
                break
            elif "factchecker" in svc.lower() or "FactChecker" in name:
                agent_hint = "factchecker"
                break
            elif "critic" in svc.lower() or "Critic" in name:
                agent_hint = "critic"
                break
        print(f"    {sid[:20]}... → {agent_hint} ({len(spans)} spans)")

    # Find the Orchestrator's session (largest span count or identified by service name)
    orchestrator_session = None
    orchestrator_spans = []
    for sid, spans in sessions.items():
        for s in spans:
            resource = s.get("resource", {}).get("attributes", {})
            svc = resource.get("service.name", "")
            if "orchestrator" in svc.lower():
                orchestrator_session = sid
                orchestrator_spans = spans
                break
        if orchestrator_session:
            break

    # Fall back to the session with the most spans
    if not orchestrator_spans:
        orchestrator_session = max(sessions, key=lambda k: len(sessions[k]))
        orchestrator_spans = sessions[orchestrator_session]

    # Include ALL spans but rewrite their session.id to match the Orchestrator's session.
    # This gives the evaluator the complete picture (including downstream agent responses)
    # while satisfying the single-session requirement.
    unified_spans = []
    for span in all_spans:
        span_copy = json.loads(json.dumps(span))  # Deep copy
        # Overwrite session.id in attributes
        if "attributes" in span_copy and isinstance(span_copy["attributes"], dict):
            span_copy["attributes"]["session.id"] = orchestrator_session
        unified_spans.append(span_copy)

    print(f"\n  Evaluating with {len(unified_spans)} unified spans (session: {orchestrator_session[:20]}...)")

    all_results = []
    for evaluator_id in EVALUATORS:
        print(f"  Running {evaluator_id}...", end=" ")
        results = run_evaluation(agentcore_client, evaluator_id, unified_spans)
        for r in results:
            r["evaluator_used"] = evaluator_id
        all_results.extend(results)

        # Show inline result
        for r in results:
            if "value" in r:
                print(f"Score: {r['value']:.2f} ({r.get('label', 'N/A')})")
            elif "errorMessage" in r:
                print(f"Error: {r['errorMessage'][:60]}")
            else:
                print("Done")

    return {"trace_id": trace_id, "span_count": len(unified_spans), "total_spans": len(all_spans), "results": all_results}


# ============================================================================
# Reporting
# ============================================================================

def print_report(evaluations):
    """Print a summary report of all evaluations."""
    print(f"\n{'=' * 70}")
    print(f"  GOAL ATTAINMENT EVALUATION REPORT")
    print(f"{'=' * 70}")
    print(f"  Generated: {datetime.now(timezone.utc).isoformat()}")
    print(f"  Sessions evaluated: {len(evaluations)}")

    for eval_data in evaluations:
        session = eval_data.get("trace_id", eval_data.get("session_id", "unknown"))
        print(f"\n{'─' * 70}")
        print(f"  Trace: {session[:40]}...")

        if "error" in eval_data:
            print(f"  Error: {eval_data['error']}")
            continue

        print(f"  Spans: {eval_data['span_count']}")
        print()

        for result in eval_data["results"]:
            evaluator = result.get("evaluator_used", result.get("evaluatorId", "Unknown"))
            if "value" in result:
                score = result["value"]
                label = result.get("label", "N/A")
                explanation = result.get("explanation", "")[:150]
                print(f"    {evaluator}")
                print(f"      Score: {score:.2f}  Label: {label}")
                if explanation:
                    print(f"      Reason: {explanation}")
            elif "errorMessage" in result:
                print(f"    {evaluator}")
                print(f"      ERROR: {result['errorMessage'][:100]}")
            print()

    # Aggregate scores
    print(f"{'─' * 70}")
    print(f"  AGGREGATE SCORES")
    print(f"{'─' * 70}")

    by_evaluator = {}
    for eval_data in evaluations:
        for result in eval_data.get("results", []):
            if "value" in result:
                name = result.get("evaluator_used", "Unknown")
                if name not in by_evaluator:
                    by_evaluator[name] = []
                by_evaluator[name].append(result["value"])

    for name, scores in by_evaluator.items():
        avg = sum(scores) / len(scores)
        print(f"    {name}: avg={avg:.2f} (n={len(scores)})")

    print(f"\n{'=' * 70}")


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Goal Attainment Evaluation")
    parser.add_argument("--session-id", type=str, help="Evaluate a specific session ID")
    parser.add_argument("--invoke", type=str, help="Invoke the agent with this prompt, then evaluate")
    parser.add_argument("--all-recent", action="store_true", help="Evaluate the last 5 sessions")
    parser.add_argument("--hours", type=int, default=None, help="Hours to look back for sessions")
    parser.add_argument("--days", type=int, default=None, help="Days to look back for sessions")
    parser.add_argument("--output", type=str, default="eval_results.json", help="Output JSON file")
    args = parser.parse_args()

    # Determine lookback window
    if args.days:
        minutes_back = args.days * 24 * 60
    elif args.hours:
        minutes_back = args.hours * 60
    else:
        minutes_back = 120  # default 2 hours

    logs_client = boto3.client("logs", region_name=REGION)
    agentcore_client = boto3.client(
        "bedrock-agentcore", region_name=REGION,
        config=Config(read_timeout=300)
    )

    # Find log group
    log_group = find_orchestrator_log_group(logs_client)
    if not log_group:
        print("ERROR: Could not find Orchestrator log group")
        sys.exit(1)

    print(f"\n  Log group: {log_group}")

    evaluations = []

    if args.invoke:
        # Invoke then evaluate
        arn = get_orchestrator_arn()
        if not arn:
            print("ERROR: Could not get Orchestrator ARN from terraform output")
            sys.exit(1)

        print(f"\n  Step 1: Invoking agent...")
        session_id, result = invoke_agent(agentcore_client, arn, args.invoke)
        print(f"  Response status: {result.get('status', 'unknown')}")

        # Wait for spans to propagate to CloudWatch
        print(f"\n  Step 2: Waiting 30s for spans to propagate...")
        time.sleep(30)

        print(f"\n  Step 3: Finding trace ID and evaluating...")
        trace_ids = get_recent_trace_ids(logs_client, log_group, limit=1, minutes_back=5)
        if not trace_ids:
            print("  No trace IDs found after invocation.")
            sys.exit(1)

        eval_result = evaluate_session(agentcore_client, logs_client, log_group, trace_ids[0], minutes_back)
        evaluations.append(eval_result)

    elif args.session_id:
        # Evaluate by trace ID (accepting session_id flag for backwards compat, treating as trace ID)
        eval_result = evaluate_session(agentcore_client, logs_client, log_group, args.session_id, minutes_back)
        evaluations.append(eval_result)

    elif args.all_recent:
        # Evaluate recent traces
        print(f"\n  Finding recent traces (last {minutes_back} minutes)...")
        trace_ids = get_recent_trace_ids(logs_client, log_group, limit=5, minutes_back=minutes_back)
        if not trace_ids:
            print("  No traces found in recent logs.")
            sys.exit(0)

        print(f"  Found {len(trace_ids)} traces")
        for trace_id in trace_ids:
            eval_result = evaluate_session(agentcore_client, logs_client, log_group, trace_id, minutes_back)
            evaluations.append(eval_result)

    else:
        # Default: evaluate most recent trace
        print(f"\n  Finding most recent trace (last {minutes_back} minutes)...")
        trace_ids = get_recent_trace_ids(logs_client, log_group, limit=1, minutes_back=minutes_back)
        if not trace_ids:
            print("  No traces found. Invoke the agent first.")
            sys.exit(0)

        eval_result = evaluate_session(agentcore_client, logs_client, log_group, trace_ids[0], minutes_back)
        evaluations.append(eval_result)

    # Report
    print_report(evaluations)

    # Save results
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(evaluations, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Results saved to: {args.output}")


if __name__ == "__main__":
    main()
