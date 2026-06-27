#!/usr/bin/env python3
"""
Multi-Agent System Test Script

This script tests a multi-agent system with Agent-to-Agent (A2A) communication.
It can work with agents deployed via any method (Terraform, CloudFormation, CDK, manual).

Usage:
    python test_multi_agent.py <orchestrator_arn> [specialist_arn]
    
    orchestrator_arn: ARN of the orchestrator agent (required)
    specialist_arn: ARN of the specialist agent (optional, for independent testing)

Examples:
    # Test orchestrator with A2A communication
    python test_multi_agent.py arn:aws:bedrock-agentcore:<region>:123456789012:runtime/orchestrator-id
    
    # Test both agents independently
    python test_multi_agent.py \\
        arn:aws:bedrock-agentcore:<region>:123456789012:runtime/orchestrator-id \\
        arn:aws:bedrock-agentcore:<region>:123456789012:runtime/specialist-id
"""

import boto3
import json
import sys
import io

# Fix Unicode output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def extract_region_from_arn(arn):
    """Extract AWS region from agent runtime ARN.

    ARN format: arn:aws:bedrock-agentcore:REGION:account:runtime/id

    Args:
        arn: Agent runtime ARN string

    Returns:
        str: AWS region code

    Raises:
        ValueError: If ARN format is invalid or region cannot be extracted
    """
    try:
        parts = arn.split(":")
        if len(parts) < 4:
            raise ValueError(
                f"Invalid ARN format: {arn}\n"
                f"Expected format: arn:aws:bedrock-agentcore:REGION:account:runtime/id"
            )

        region = parts[3]
        if not region:
            raise ValueError(
                f"Region not found in ARN: {arn}\n"
                f"Expected format: arn:aws:bedrock-agentcore:REGION:account:runtime/id"
            )

        return region

    except IndexError:
        raise ValueError(
            f"Invalid ARN format: {arn}\n"
            f"Expected format: arn:aws:bedrock-agentcore:REGION:account:runtime/id"
        )


def progress_indicator(stop_event):
    """Show elapsed time while waiting for response."""
    import time
    from datetime import datetime, timezone
    start = datetime.now(timezone.utc)
    while not stop_event.is_set():
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        print(f"\r  Waiting... {elapsed:.0f}s", end="", flush=True)
        time.sleep(1)
    print("\r" + " " * 30 + "\r", end="", flush=True)


def test_agent(client, agent_arn, agent_name, prompt):
    """Test a single agent with a given prompt

    Args:
        client: boto3 bedrock-agentcore client
        agent_arn: ARN of the agent runtime
        agent_name: Name for display purposes
        prompt: Test prompt to send
    """
    import threading

    print(f"\nPrompt: '{prompt}'")
    print("-" * 80)

    stop_event = threading.Event()
    spinner = threading.Thread(target=progress_indicator, args=(stop_event,), daemon=True)
    spinner.start()

    try:
        import uuid
        session_id = f"test-{uuid.uuid4()}"
        response = client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            runtimeSessionId=session_id,
            qualifier="DEFAULT",
            payload=json.dumps({"prompt": prompt}),
        )

        stop_event.set()
        spinner.join()

        print(f"Status: {response['ResponseMetadata']['HTTPStatusCode']}")
        print(f"Content Type: {response.get('contentType', 'N/A')}")

        # Read the streaming response body
        response_text = ""
        if "response" in response:
            response_body = response["response"].read()
            response_text = response_body.decode("utf-8")

        if response_text:
            try:
                result = json.loads(response_text)
                response_content = result.get("response", response_text)
                # Truncate long responses for readability
                if len(response_content) > 500:
                    print(f"\n✅ Response:\n{response_content[:500]}...")
                    print("\n[Response truncated - full output in test_results.json]")
                else:
                    print(f"\n✅ Response:\n{response_content}")
            except json.JSONDecodeError:
                if len(response_text) > 500:
                    print(f"\n✅ Response:\n{response_text[:500]}...")
                else:
                    print(f"\n✅ Response:\n{response_text}")
        else:
            print("\n⚠️  No response content received")

        return True, response_text

    except Exception as e:
        stop_event.set()
        spinner.join()
        print(f"\n❌ Error testing {agent_name}: {e}")
        return False, str(e)


def test_multi_agent(orchestrator_arn, specialist_arn=None):
    """Test the multi-agent system

    Args:
        orchestrator_arn: Orchestrator agent runtime ARN (required)
        specialist_arn: Specialist agent runtime ARN (optional)
    """

    # Extract region from orchestrator ARN
    try:
        region = extract_region_from_arn(orchestrator_arn)
    except ValueError as e:
        print(f"\n❌ ERROR: {e}\n")
        sys.exit(1)

    print("\n" + "=" * 80)
    print("MULTI-AGENT SYSTEM TEST")
    print("=" * 80)
    print(f"\nOrchestrator Agent ARN: {orchestrator_arn}")
    if specialist_arn:
        print(f"Specialist Agent ARN: {specialist_arn}")
    else:
        print("Specialist Agent: Not provided (will test Orchestrator only)")
    print(f"Region: {region}")

    # Create bedrock-agentcore client with extracted region
    # Extended timeout for cold starts (container startup can take 2-3 minutes)
    from botocore.config import Config

    config = Config(read_timeout=300, connect_timeout=10, retries={"max_attempts": 2})
    agentcore_client = boto3.client(
        "bedrock-agentcore", region_name=region, config=config
    )

    test_results = []
    full_responses = []

    # Test 1: Simple query to Orchestrator
    print("\n" + "=" * 80)
    print("TEST 1: Simple Query (Orchestrator)")
    print("=" * 80)
    prompt = "Hello! Can you introduce yourself and your capabilities?"
    passed, response = test_agent(agentcore_client, orchestrator_arn, "Orchestrator", prompt)
    test_results.append(("Simple Query", passed))
    full_responses.append({"test": "Simple Query", "prompt": prompt, "passed": passed, "response": response})

    # Test 2: Complex query triggering A2A communication
    print("\n" + "=" * 80)
    print("TEST 2: Complex Query with A2A Communication (Specialist)")
    print("=" * 80)
    prompt = "I need expert analysis. Please coordinate with the specialist agent to provide a comprehensive explanation of cloud computing architectures and best practices."
    passed, response = test_agent(agentcore_client, orchestrator_arn, "Orchestrator", prompt)
    test_results.append(("A2A Communication - Specialist", passed))
    full_responses.append({"test": "A2A Communication - Specialist", "prompt": prompt, "passed": passed, "response": response})

    # Test 3: Fact-checking query triggering Fact Checker agent
    print("\n" + "=" * 80)
    print("TEST 3: Fact Check Query (Fact Checker Agent)")
    print("=" * 80)
    prompt = "Please fact-check this claim: 'Amazon S3 provides 99.999999999% (11 nines) durability for objects stored across multiple Availability Zones.'"
    passed, response = test_agent(agentcore_client, orchestrator_arn, "Orchestrator", prompt)
    test_results.append(("A2A Communication - Fact Checker", passed))
    full_responses.append({"test": "A2A Communication - Fact Checker", "prompt": prompt, "passed": passed, "response": response})

    # Test 4: Multi-agent query triggering BOTH specialist and fact checker
    print("\n" + "=" * 80)
    print("TEST 4: Multi-Agent Query (Both Specialist AND Fact Checker)")
    print("=" * 80)
    prompt = "I need you to do two things: First, get a detailed analysis from the specialist about how serverless computing works. Then, fact-check this claim: 'AWS Lambda functions can run for a maximum of 15 minutes.' Please use both agents."
    passed, response = test_agent(agentcore_client, orchestrator_arn, "Orchestrator", prompt)
    test_results.append(("A2A Multi-Agent Communication", passed))
    full_responses.append({"test": "A2A Multi-Agent Communication", "prompt": prompt, "passed": passed, "response": response})

    # Test 5: Quality evaluation triggering Critic agent
    print("\n" + "=" * 80)
    print("TEST 5: Quality Evaluation (Critic Agent)")
    print("=" * 80)
    prompt = "Get the specialist to explain what Kubernetes is, then use the critic to evaluate the quality of the response. Tell me what score the critic gave."
    passed, response = test_agent(agentcore_client, orchestrator_arn, "Orchestrator", prompt)
    test_results.append(("A2A Communication - Critic", passed))
    full_responses.append({"test": "A2A Communication - Critic", "prompt": prompt, "passed": passed, "response": response})

    # Test 6: Full feedback loop (Specialist → Critic → retry)
    print("\n" + "=" * 80)
    print("TEST 6: Quality Feedback Loop (Specialist -> Critic -> Improved Response)")
    print("=" * 80)
    prompt = "This is an important question that needs a high-quality answer. Ask the specialist to explain API gateway patterns, then have the critic evaluate it. If the critic scores it below 8, ask the specialist again incorporating the critic's feedback. I want the best possible answer."
    passed, response = test_agent(agentcore_client, orchestrator_arn, "Orchestrator", prompt)
    test_results.append(("A2A Quality Feedback Loop", passed))
    full_responses.append({"test": "A2A Quality Feedback Loop", "prompt": prompt, "passed": passed, "response": response})

    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)

    all_passed = True
    for test_name, passed in test_results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{status} - {test_name}")
        if not passed:
            all_passed = False

    print("\n" + "=" * 80)
    if all_passed:
        print("✅ ALL TESTS PASSED")
    else:
        print("⚠️  SOME TESTS FAILED")
    print("=" * 80)

    # Save full responses to file
    output_file = "test_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(full_responses, f, indent=2, ensure_ascii=False)

    # Save CSV for easy viewing in spreadsheets
    import csv
    csv_file = "test_results.csv"
    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Test", "Passed", "Prompt", "Response"])
        for entry in full_responses:
            # Parse the response JSON to extract the text
            response_text = entry.get("response", "")
            try:
                parsed = json.loads(response_text)
                display_response = parsed.get("response", parsed.get("error", response_text))
            except (json.JSONDecodeError, TypeError):
                display_response = response_text
            writer.writerow([
                entry.get("test", ""),
                entry.get("passed", False),
                entry.get("prompt", ""),
                display_response,
            ])

    print(f"\nFull responses saved to: {output_file}")
    print(f"CSV export saved to: {csv_file}\n")

    return all_passed


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print(__doc__)
        print("\n❌ ERROR: Agent runtime ARN is required")
        print("\nTo get your agent ARN:")
        print("  - Terraform: terraform output orchestrator_runtime_arn")
        print(
            "  - CloudFormation: aws cloudformation describe-stacks --stack-name <stack> --query 'Stacks[0].Outputs'"
        )
        print("  - CDK: cdk deploy --outputs-file outputs.json")
        print("  - Console: Check Bedrock Agent Core console")
        sys.exit(1)

    orchestrator_arn = sys.argv[1]
    specialist_arn = sys.argv[2] if len(sys.argv) > 2 else None

    # Validate ARN format
    if not orchestrator_arn.startswith("arn:aws:bedrock-agentcore:"):
        print(f"\n❌ ERROR: Invalid ARN format for orchestrator: {orchestrator_arn}")
        print(
            "Expected format: arn:aws:bedrock-agentcore:region:account:runtime/runtime-id"
        )
        sys.exit(1)

    if specialist_arn and not specialist_arn.startswith("arn:aws:bedrock-agentcore:"):
        print(f"\n❌ ERROR: Invalid ARN format for specialist: {specialist_arn}")
        print(
            "Expected format: arn:aws:bedrock-agentcore:region:account:runtime/runtime-id"
        )
        sys.exit(1)

    # Run tests
    success = test_multi_agent(orchestrator_arn, specialist_arn)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
