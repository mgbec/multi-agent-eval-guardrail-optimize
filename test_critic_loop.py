"""
Test the Critic quality feedback loop.

This script specifically exercises the Critic agent and the multi-hop
feedback pattern:
  1. Orchestrator → Specialist (initial answer)
  2. Orchestrator → Critic (evaluate quality)
  3. Orchestrator → Specialist (retry with feedback, if score < threshold)
  4. Final response

Usage:
    python test_critic_loop.py                          # Uses Terraform output for ARN
    python test_critic_loop.py <orchestrator_arn>       # Explicit ARN
    python test_critic_loop.py --all                    # Run all test scenarios
"""

import boto3
import json
import sys
import io
import subprocess
import argparse
from botocore.config import Config
from datetime import datetime, timezone

# Fix Unicode on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# ============================================================================
# Test Scenarios
# ============================================================================

SCENARIOS = [
    {
        "name": "Basic Critic Evaluation",
        "description": "Specialist answers, Critic scores it",
        "prompt": "Ask the specialist to explain what Docker containers are. Then have the critic evaluate the response quality. Report the critic's score and feedback.",
        "expected_agents": ["specialist", "critic"],
    },
    {
        "name": "Feedback Loop - Improve Low Quality",
        "description": "Specialist answers, Critic scores low, Specialist retries with feedback",
        "prompt": "This is critical - I need the highest quality answer possible. Ask the specialist for a brief explanation of microservices (keep it short). Then have the critic evaluate it. If the score is below 9, retry with the critic's suggestions for improvement. Show me both the original and improved versions.",
        "expected_agents": ["specialist", "critic"],
    },
    {
        "name": "Critic on Fact Checker Output",
        "description": "Fact Checker verifies a claim, Critic evaluates the verdict quality",
        "prompt": "First, have the fact checker verify this claim: 'Kubernetes was originally designed by Google.' Then have the critic evaluate whether the fact checker's response is thorough and well-reasoned. Report both results.",
        "expected_agents": ["factchecker", "critic"],
    },
    {
        "name": "Full Pipeline - Research + Verify + Evaluate",
        "description": "Specialist researches, Fact Checker verifies, Critic evaluates the whole thing",
        "prompt": "I need a comprehensive and verified answer. Ask the specialist about how load balancers work. Then fact-check the claim that 'AWS ALB operates at Layer 7 of the OSI model.' Finally, have the critic evaluate the overall quality of the combined response. Synthesize everything into a final answer.",
        "expected_agents": ["specialist", "factchecker", "critic"],
    },
    {
        "name": "Critic Disagreement Test",
        "description": "Tests what happens when Critic gives a low score",
        "prompt": "Ask the specialist to answer this in exactly one sentence: 'Explain distributed systems, including CAP theorem, consensus algorithms, replication strategies, and failure modes.' The answer will necessarily be incomplete. Then have the critic evaluate it and provide specific improvement suggestions.",
        "expected_agents": ["specialist", "critic"],
    },
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


def invoke_orchestrator(client, arn, prompt):
    """Invoke the orchestrator and return the parsed response."""
    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        qualifier="DEFAULT",
        payload=json.dumps({"prompt": prompt}),
    )
    body = response["response"].read().decode("utf-8")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"status": "error", "raw": body}


def progress_indicator(stop_event, start_time):
    """Show elapsed time while waiting for response."""
    import time
    while not stop_event.is_set():
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        print(f"\r  Waiting... {elapsed:.0f}s", end="", flush=True)
        time.sleep(1)
    print("\r" + " " * 30 + "\r", end="", flush=True)


def run_scenario(client, arn, scenario, index):
    """Run a single test scenario."""
    import threading

    print(f"\n{'=' * 70}")
    print(f"  TEST {index}: {scenario['name']}")
    print(f"  {scenario['description']}")
    print(f"{'=' * 70}")
    print(f"\n  Prompt: {scenario['prompt'][:100]}...")
    print(f"  Expected agents: {scenario['expected_agents']}")
    print()

    start_time = datetime.now(timezone.utc)
    stop_event = threading.Event()
    spinner = threading.Thread(target=progress_indicator, args=(stop_event, start_time), daemon=True)
    spinner.start()

    try:
        result = invoke_orchestrator(client, arn, scenario["prompt"])
    except Exception as e:
        stop_event.set()
        spinner.join()
        print(f"  ERROR: {e}")
        return False

    stop_event.set()
    spinner.join()

    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

    status = result.get("status", "unknown")
    agent = result.get("agent", "unknown")
    response_text = result.get("response", result.get("error", ""))

    print(f"  Status:  {status}")
    print(f"  Agent:   {agent}")
    print(f"  Time:    {elapsed:.1f}s")

    if status == "success":
        # Show response (truncated)
        if len(response_text) > 600:
            print(f"\n  Response (truncated):\n  {response_text[:600]}...")
        else:
            print(f"\n  Response:\n  {response_text}")

        # Check for critic-related content in response
        has_critic_content = any(
            keyword in response_text.lower()
            for keyword in ["score", "critic", "evaluation", "rating", "/10", "out of 10", "needs_improvement", "excellent", "good"]
        )
        if has_critic_content:
            print(f"\n  [OK] Response contains quality evaluation content")
        elif "critic" in scenario["expected_agents"]:
            print(f"\n  [WARN] Expected critic evaluation but none found in response")

        print(f"\n  PASSED")
        return True
    else:
        print(f"\n  Error: {response_text[:300]}")
        print(f"\n  FAILED")
        return False


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Test Critic Agent feedback loop")
    parser.add_argument("arn", nargs="?", help="Orchestrator ARN (auto-detected if omitted)")
    parser.add_argument("--all", action="store_true", help="Run all scenarios (default: first 2)")
    parser.add_argument("--scenario", type=int, help="Run a specific scenario (1-5)")
    args = parser.parse_args()

    # Get ARN
    arn = args.arn or get_orchestrator_arn()
    if not arn:
        print("ERROR: Could not determine Orchestrator ARN.")
        print("Either pass it as an argument or run from the Terraform directory.")
        sys.exit(1)

    # Create client
    region = arn.split(":")[3]
    client = boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(read_timeout=300, connect_timeout=10),
    )

    print(f"\n{'=' * 70}")
    print(f"  CRITIC AGENT FEEDBACK LOOP TEST")
    print(f"{'=' * 70}")
    print(f"  Orchestrator: {arn.split('/')[-1]}")
    print(f"  Region: {region}")

    # Select scenarios
    if args.scenario:
        if 1 <= args.scenario <= len(SCENARIOS):
            scenarios_to_run = [(args.scenario, SCENARIOS[args.scenario - 1])]
        else:
            print(f"  ERROR: Scenario must be 1-{len(SCENARIOS)}")
            sys.exit(1)
    elif args.all:
        scenarios_to_run = list(enumerate(SCENARIOS, 1))
    else:
        # Default: run first 2 (quick test)
        scenarios_to_run = list(enumerate(SCENARIOS[:2], 1))

    print(f"  Scenarios: {len(scenarios_to_run)}")

    # Run
    results = []
    for index, scenario in scenarios_to_run:
        passed = run_scenario(client, arn, scenario, index)
        results.append((scenario["name"], passed))

    # Summary
    print(f"\n{'=' * 70}")
    print(f"  SUMMARY")
    print(f"{'=' * 70}")

    all_passed = True
    for name, passed in results:
        icon = "PASSED" if passed else "FAILED"
        print(f"  [{icon}] {name}")
        if not passed:
            all_passed = False

    print(f"\n  {'ALL TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}")
    print(f"{'=' * 70}\n")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
