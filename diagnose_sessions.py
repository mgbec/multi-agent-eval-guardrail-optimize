"""
Diagnose which agent each session belongs to.

Takes the failed session IDs from a recommendation result and checks
which agent (Orchestrator, Specialist, Fact Checker, Critic) each session
belongs to. This helps explain why certain sessions fail evaluation.

Usage:
    python diagnose_sessions.py                          # Uses recommendation_result.json
    python diagnose_sessions.py --file my_result.json    # Custom result file
    python diagnose_sessions.py --sessions id1 id2 id3   # Check specific session IDs
"""

import boto3
import json
import sys
import io
import time
import argparse
from datetime import datetime, timedelta, timezone

# Fix Unicode on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

REGION = "us-east-1"


def get_session_agent(logs_client, session_id):
    """Determine which agent a session belongs to by checking spans."""
    # Check aws/spans for this session
    now = datetime.now(timezone.utc)
    start = int((now - timedelta(days=7)).timestamp())
    end = int(now.timestamp())

    query = f"""fields @timestamp, @message
    | filter @message like "{session_id}"
    | limit 5"""

    qid = logs_client.start_query(
        logGroupName="aws/spans",
        startTime=start, endTime=end,
        queryString=query
    )["queryId"]

    time.sleep(3)
    result = logs_client.get_query_results(queryId=qid)

    if not result["results"]:
        return {"agent": "UNKNOWN", "service": "no spans found", "has_input": False, "has_output": False}

    # Parse the first span to find the service name
    agent_name = "UNKNOWN"
    service_name = ""
    has_input = False
    has_output = False

    for row in result["results"]:
        for field in row:
            if field["field"] == "@message":
                try:
                    span = json.loads(field["value"])
                    # Check resource attributes for service name
                    resource = span.get("resource", {}).get("attributes", {})
                    svc = resource.get("service.name", "")
                    if svc:
                        service_name = svc

                    # Identify agent from service name
                    if "orchestrator" in svc.lower() or "Orchestrator" in svc:
                        agent_name = "Orchestrator"
                    elif "specialist" in svc.lower() or "Specialist" in svc:
                        agent_name = "Specialist"
                    elif "factchecker" in svc.lower() or "FactChecker" in svc:
                        agent_name = "Fact Checker"
                    elif "critic" in svc.lower() or "Critic" in svc:
                        agent_name = "Critic"

                    # Check if span has input/output (needed for GoalSuccessRate)
                    name = span.get("name", "")
                    attrs = span.get("attributes", {})
                    if "invoke_agent" in name or "POST /invocations" in name:
                        has_input = True
                    if attrs.get("http.status_code") == 200 or attrs.get("http.response.status_code") == 200:
                        has_output = True

                except (json.JSONDecodeError, TypeError):
                    pass

    return {
        "agent": agent_name,
        "service": service_name,
        "has_input": has_input,
        "has_output": has_output,
    }


def main():
    parser = argparse.ArgumentParser(description="Diagnose failed recommendation sessions")
    parser.add_argument("--file", type=str, default="recommendation_result.json", help="Recommendation result file")
    parser.add_argument("--sessions", nargs="+", help="Specific session IDs to check")
    args = parser.parse_args()

    logs_client = boto3.client("logs", region_name=REGION)

    # Get session IDs
    if args.sessions:
        session_ids = args.sessions
        source = "command line"
    else:
        try:
            with open(args.file, "r") as f:
                data = json.load(f)
            # Extract failed session IDs from the error message
            error_msg = ""
            rec_result = data.get("recommendationResult", {})
            for key in rec_result:
                err = rec_result[key].get("errorMessage", "")
                if err:
                    error_msg = err
                    break

            if "Skipped session IDs" in error_msg:
                # Parse the list from the error message
                start_idx = error_msg.find("[")
                end_idx = error_msg.find("]") + 1
                session_ids = json.loads(error_msg[start_idx:end_idx].replace("'", '"'))
            else:
                print("No failed sessions found in recommendation result.")
                sys.exit(0)
            source = args.file
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"ERROR: Could not read {args.file}: {e}")
            sys.exit(1)

    print(f"\n{'=' * 70}")
    print(f"  SESSION DIAGNOSIS")
    print(f"{'=' * 70}")
    print(f"  Source: {source}")
    print(f"  Sessions to check: {len(session_ids)}")
    print()

    results = []
    agent_counts = {}

    for sid in session_ids:
        info = get_session_agent(logs_client, sid)
        results.append({"session_id": sid, **info})
        agent = info["agent"]
        agent_counts[agent] = agent_counts.get(agent, 0) + 1
        print(f"  {sid[:20]}... → {info['agent']:15} (service: {info['service'][:30]})")

    print(f"\n{'─' * 70}")
    print(f"  SUMMARY")
    print(f"{'─' * 70}")
    print(f"\n  Failed sessions by agent:")
    for agent, count in sorted(agent_counts.items(), key=lambda x: -x[1]):
        print(f"    {agent}: {count}")

    print(f"\n  Analysis:")
    orch_count = agent_counts.get("Orchestrator", 0)
    non_orch = len(session_ids) - orch_count
    if non_orch > 0:
        print(f"    {non_orch} sessions belong to downstream agents (Specialist/Fact Checker/Critic).")
        print(f"    These fail GoalSuccessRate because they don't have the original user question.")
        print(f"    This is expected — only Orchestrator sessions are meaningful for goal evaluation.")
    if orch_count > 0:
        print(f"    {orch_count} Orchestrator sessions failed — these may have incomplete spans or errors.")

    # Save detailed results
    output = {
        "diagnosed_at": datetime.now(timezone.utc).isoformat(),
        "total_failed": len(session_ids),
        "by_agent": agent_counts,
        "sessions": results,
    }
    output_file = "session_diagnosis.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Details saved to: {output_file}")
    print(f"\n{'=' * 70}\n")


if __name__ == "__main__":
    main()
