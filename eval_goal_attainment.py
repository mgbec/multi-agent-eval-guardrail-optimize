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


def get_recent_session_ids(logs_client, log_group, limit=5):
    """Get the most recent session IDs from traces."""
    query = f"""fields @timestamp, attributes.`session.id` as session_id
    | filter ispresent(attributes.`session.id`)
    | stats max(@timestamp) as last_seen by session_id
    | sort last_seen desc
    | limit {limit}"""

    results = query_logs(logs_client, log_group, query)
    sessions = []
    for row in results:
        for field in row:
            if field["field"] == "session_id" and field["value"]:
                sessions.append(field["value"])
    return sessions


def collect_session_spans(logs_client, log_group, session_id):
    """Collect all span logs for a given session."""
    # Query the agent runtime logs
    query = f"""fields @timestamp, @message
    | filter ispresent(scope.name) and ispresent(attributes.`session.id`)
    | filter attributes.`session.id` = "{session_id}"
    | sort @timestamp asc"""

    runtime_results = query_logs(logs_client, log_group, query)

    # Also query aws/spans
    aws_spans_query = f"""fields @timestamp, @message
    | filter `session.id` = "{session_id}" or attributes.`session.id` = "{session_id}"
    | sort @timestamp asc"""

    aws_span_results = query_logs(logs_client, "aws/spans", aws_spans_query)

    # Extract JSON messages
    spans = []
    for results in [runtime_results, aws_span_results]:
        for row in results:
            for field in row:
                if field["field"] == "@message" and field["value"].strip().startswith("{"):
                    try:
                        spans.append(json.loads(field["value"]))
                    except json.JSONDecodeError:
                        pass

    return spans


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


def evaluate_session(agentcore_client, logs_client, log_group, session_id):
    """Run all evaluators against a session and return results."""
    print(f"\n  Collecting spans for session: {session_id[:20]}...")

    spans = collect_session_spans(logs_client, log_group, session_id)
    if not spans:
        return {"session_id": session_id, "error": "No spans found", "results": []}

    print(f"  Found {len(spans)} spans")

    all_results = []
    for evaluator_id in EVALUATORS:
        print(f"  Running {evaluator_id}...", end=" ")
        results = run_evaluation(agentcore_client, evaluator_id, spans)
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

    return {"session_id": session_id, "span_count": len(spans), "results": all_results}


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
        session = eval_data["session_id"]
        print(f"\n{'─' * 70}")
        print(f"  Session: {session[:40]}...")

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
    parser.add_argument("--output", type=str, default="eval_results.json", help="Output JSON file")
    args = parser.parse_args()

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

        print(f"\n  Step 3: Evaluating...")
        eval_result = evaluate_session(agentcore_client, logs_client, log_group, session_id)
        evaluations.append(eval_result)

    elif args.session_id:
        # Evaluate specific session
        eval_result = evaluate_session(agentcore_client, logs_client, log_group, args.session_id)
        evaluations.append(eval_result)

    elif args.all_recent:
        # Evaluate recent sessions
        print(f"\n  Finding recent sessions...")
        sessions = get_recent_session_ids(logs_client, log_group, limit=5)
        if not sessions:
            print("  No sessions found in recent logs.")
            sys.exit(0)

        print(f"  Found {len(sessions)} sessions")
        for session_id in sessions:
            eval_result = evaluate_session(agentcore_client, logs_client, log_group, session_id)
            evaluations.append(eval_result)

    else:
        # Default: evaluate most recent session
        print(f"\n  Finding most recent session...")
        sessions = get_recent_session_ids(logs_client, log_group, limit=1)
        if not sessions:
            print("  No sessions found. Invoke the agent first.")
            sys.exit(0)

        eval_result = evaluate_session(agentcore_client, logs_client, log_group, sessions[0])
        evaluations.append(eval_result)

    # Report
    print_report(evaluations)

    # Save results
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(evaluations, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Results saved to: {args.output}")


if __name__ == "__main__":
    main()
