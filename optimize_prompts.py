"""
AgentCore Optimization - Generate prompt and tool description recommendations.

This script uses AgentCore's Recommendations API to analyze real agent traces
and generate AI-improved system prompts and tool descriptions.

Usage:
    python optimize_prompts.py                          # Recommend improved Orchestrator prompt
    python optimize_prompts.py --agent orchestrator     # Orchestrator system prompt
    python optimize_prompts.py --agent specialist       # Specialist system prompt
    python optimize_prompts.py --tools                  # Tool description recommendations
    python optimize_prompts.py --days 7                 # Use last 7 days of traces (default)
    python optimize_prompts.py --apply                  # Apply recommendation (updates agent code)

Prerequisites:
    - Agent must have been invoked (traces must exist in CloudWatch)
    - CloudWatch Transaction Search must be enabled
    - pip install boto3
"""

import boto3
import json
import sys
import io
import time
import argparse
from datetime import datetime, timedelta, timezone
from botocore.config import Config

# Fix Unicode on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

REGION = "us-east-1"
ACCOUNT_ID = None  # Populated at runtime

# ============================================================================
# Current prompts (mirrors what's in the agent code)
# ============================================================================

ORCHESTRATOR_PROMPT = """You are an orchestrator agent that coordinates between specialized agents.
    You have three tools available:

    1. call_specialist_agent - For detailed analysis, explanations, and complex research tasks
    2. call_factchecker_agent - For verifying claims, checking facts, and assessing truthfulness
    3. call_critic_agent - For evaluating the quality of responses from other agents

    Routing guidelines:
    - For questions requiring detailed analysis or explanation → use call_specialist_agent
    - For verifying specific claims or statements → use call_factchecker_agent
    - For complex queries that involve both analysis AND fact verification → use BOTH specialist and factchecker
    - For simple greetings or basic questions → handle directly yourself

    Quality feedback loop (use for important questions):
    - After getting a response from the specialist, use call_critic_agent to evaluate it
    - Pass the critic both the original question AND the specialist's response
    - If the critic scores below 7/10, call the specialist again with the critic's feedback
    - Include the critic's suggestion in your retry prompt to the specialist
    - IMPORTANT: Maximum 2 retries allowed. After 2 retries, use the best response you have.
    - Present the final (improved) response to the user

    When using multiple tools, synthesize their responses into a coherent answer.
    Always mention if you used the critic to improve a response."""

SPECIALIST_PROMPT = """You are a specialist analytical agent with web search capabilities.
    You are an expert at analyzing data and providing detailed insights.
    
    IMPORTANT: You have a web_search tool available. Use it when:
    - The question is about something recent or unfamiliar to you
    - You need current/up-to-date information
    - You're not confident in your knowledge of the topic
    - The user asks about a specific product, project, or event you don't recognize
    
    Always use web_search before saying you don't know something.
    When you search, synthesize the results into a clear, comprehensive answer.
    Cite sources by including relevant URLs."""

FACTCHECKER_PROMPT = """You are a fact-checking agent specialized in evaluating claims.
    You have a web_search tool available to verify facts against current sources.

    When given a statement or claim, you must:
    1. Identify the core claim being made
    2. Use web_search to find evidence if you're not 100% certain or if it involves recent info
    3. Evaluate the claim based on your knowledge AND search results
    4. Provide a confidence assessment: HIGH, MEDIUM, or LOW
    5. Explain your reasoning briefly with sources

    Always respond in this format:
    CLAIM: [restate the claim]
    VERDICT: [TRUE / FALSE / PARTIALLY TRUE / UNVERIFIABLE]
    CONFIDENCE: [HIGH / MEDIUM / LOW]
    REASONING: [brief explanation with sources if searched]

    Be concise and precise. Focus on factual accuracy, not opinions.
    Always search when unsure — never guess."""

CRITIC_PROMPT = """You are a Critic agent that evaluates the quality of responses from other AI agents.

    When given a response to evaluate, you must score it and provide actionable feedback.

    Always respond in this exact JSON format:
    {
        "score": <number 1-10>,
        "verdict": "<EXCELLENT | GOOD | NEEDS_IMPROVEMENT | POOR>",
        "strengths": ["<strength 1>", "<strength 2>"],
        "weaknesses": ["<weakness 1>", "<weakness 2>"],
        "suggestion": "<one specific, actionable improvement suggestion>",
        "missing_elements": ["<missing element 1>", "<missing element 2>"]
    }

    Scoring criteria:
    - 9-10: Comprehensive, well-structured, accurate, with examples and sources
    - 7-8: Good coverage, mostly accurate, but missing some depth or examples
    - 5-6: Addresses the question but lacks detail, structure, or accuracy
    - 3-4: Partially relevant, significant gaps or inaccuracies
    - 1-2: Off-topic, incorrect, or unhelpful

    IMPORTANT: Always output valid JSON. Nothing else."""

TOOL_DESCRIPTIONS = [
    {
        "toolName": "call_specialist_agent",
        "toolDescription": "Call the specialist agent for detailed analysis or complex tasks. Use this tool when you need expert analysis, detailed explanations, or in-depth information on a topic.",
    },
    {
        "toolName": "call_factchecker_agent",
        "toolDescription": "Call the fact checker agent to verify a claim or statement. Use this tool when you need to verify facts, check accuracy of statements, or assess the truthfulness of a claim.",
    },
    {
        "toolName": "call_critic_agent",
        "toolDescription": "Call the critic agent to evaluate the quality of a response. Use this tool after receiving a response from the specialist or fact checker to assess its quality and get improvement suggestions.",
    },
]

PROMPTS = {
    "orchestrator": ORCHESTRATOR_PROMPT,
    "specialist": SPECIALIST_PROMPT,
    "factchecker": FACTCHECKER_PROMPT,
    "critic": CRITIC_PROMPT,
}


# ============================================================================
# Helpers
# ============================================================================

def get_account_id():
    """Get AWS account ID."""
    global ACCOUNT_ID
    if not ACCOUNT_ID:
        sts = boto3.client("sts", region_name=REGION)
        ACCOUNT_ID = sts.get_caller_identity()["Account"]
    return ACCOUNT_ID


def find_log_group_arn(agent_name):
    """Find the log group ARN for a given agent."""
    logs_client = boto3.client("logs", region_name=REGION)

    prefix_map = {
        "orchestrator": "OrchestratorAgent",
        "specialist": "SpecialistAgent",
        "factchecker": "FactCheckerAgent",
        "critic": "CriticAgent",
    }

    prefix = f"/aws/bedrock-agentcore/runtimes/agentcore_multi_agent_{prefix_map[agent_name]}"
    response = logs_client.describe_log_groups(logGroupNamePrefix=prefix)
    groups = response.get("logGroups", [])
    if not groups:
        return None

    # Most recent
    group = sorted(groups, key=lambda g: g.get("creationTime", 0), reverse=True)[0]
    log_group_name = group["logGroupName"]
    account_id = get_account_id()
    return f"arn:aws:logs:{REGION}:{account_id}:log-group:{log_group_name}"


def get_service_name(agent_name):
    """Get the OTEL service name for an agent."""
    name_map = {
        "orchestrator": "orchestrator-agent",
        "specialist": "specialist-agent",
        "factchecker": "factchecker-agent",
        "critic": "critic-agent",
    }
    return name_map[agent_name]


# ============================================================================
# Recommendation Functions
# ============================================================================

def request_system_prompt_recommendation(client, agent_name, days, evaluator_id):
    """Request an AI-generated system prompt improvement."""
    log_group_arn = find_log_group_arn(agent_name)
    if not log_group_arn:
        print(f"  ERROR: Could not find log group for {agent_name}")
        return None

    service_name = get_service_name(agent_name)
    current_prompt = PROMPTS[agent_name]
    now = datetime.now(timezone.utc)

    print(f"  Agent: {agent_name}")
    print(f"  Log group: {log_group_arn.split(':log-group:')[1]}")
    print(f"  Service: {service_name}")
    print(f"  Evaluator: {evaluator_id}")
    print(f"  Trace window: last {days} days")
    print(f"  Current prompt length: {len(current_prompt)} chars")
    print()

    try:
        response = client.start_recommendation(
            recommendationType="SYSTEM_PROMPT",
            evaluatorId=evaluator_id,
            systemPrompt={"text": current_prompt},
            agentTraces={
                "cloudwatchLogs": {
                    "logGroupArns": [log_group_arn],
                    "serviceNames": [service_name],
                    "startTime": now - timedelta(days=days),
                    "endTime": now,
                }
            },
        )
        return response
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def request_tool_description_recommendation(client, days, evaluator_id):
    """Request AI-generated tool description improvements."""
    log_group_arn = find_log_group_arn("orchestrator")
    if not log_group_arn:
        print("  ERROR: Could not find Orchestrator log group")
        return None

    service_name = get_service_name("orchestrator")
    now = datetime.now(timezone.utc)

    print(f"  Target: Orchestrator tool descriptions")
    print(f"  Evaluator: {evaluator_id}")
    print(f"  Trace window: last {days} days")
    print(f"  Tools: {[t['toolName'] for t in TOOL_DESCRIPTIONS]}")
    print()

    try:
        response = client.start_recommendation(
            recommendationType="TOOL_DESCRIPTION",
            evaluatorId=evaluator_id,
            toolDescription={
                "toolDescriptionText": {"tools": TOOL_DESCRIPTIONS}
            },
            agentTraces={
                "cloudwatchLogs": {
                    "logGroupArns": [log_group_arn],
                    "serviceNames": [service_name],
                    "startTime": now - timedelta(days=days),
                    "endTime": now,
                }
            },
        )
        return response
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def poll_recommendation(client, recommendation_id, max_wait=300):
    """Poll until recommendation completes."""
    print(f"  Waiting for recommendation {recommendation_id}...")
    start = time.time()
    while time.time() - start < max_wait:
        try:
            response = client.get_recommendation(recommendationId=recommendation_id)
            status = response.get("status", "UNKNOWN")
            if status in ("COMPLETED", "FAILED"):
                return response
            elapsed = int(time.time() - start)
            print(f"\r  Status: {status} ({elapsed}s)...", end="", flush=True)
        except Exception as e:
            print(f"\n  Poll error: {e}")
        time.sleep(5)
    print("\n  Timed out waiting for recommendation")
    return None


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="AgentCore Optimization - Prompt Recommendations")
    parser.add_argument("--agent", type=str, default="orchestrator",
                        choices=["orchestrator", "specialist", "factchecker", "critic"],
                        help="Which agent's prompt to optimize (default: orchestrator)")
    parser.add_argument("--tools", action="store_true",
                        help="Optimize tool descriptions instead of system prompt")
    parser.add_argument("--days", type=int, default=7,
                        help="Days of traces to analyze (default: 7)")
    parser.add_argument("--evaluator", type=str, default="Builtin.GoalSuccessRate",
                        help="Evaluator to optimize for (default: Builtin.GoalSuccessRate)")
    parser.add_argument("--output", type=str, default="recommendation_result.json",
                        help="Output file for recommendation")
    parser.add_argument("--no-wait", action="store_true",
                        help="Submit and return immediately without waiting")
    args = parser.parse_args()

    client = boto3.client("bedrock-agentcore", region_name=REGION, config=Config(read_timeout=300))

    print(f"\n{'=' * 60}")
    print(f"  AGENTCORE OPTIMIZATION - PROMPT RECOMMENDATIONS")
    print(f"{'=' * 60}\n")

    if args.tools:
        print("  Mode: Tool Description Optimization\n")
        response = request_tool_description_recommendation(client, args.days, args.evaluator)
    else:
        print(f"  Mode: System Prompt Optimization ({args.agent})\n")
        response = request_system_prompt_recommendation(client, args.agent, args.days, args.evaluator)

    if not response:
        sys.exit(1)

    recommendation_id = response.get("recommendationId", "")
    print(f"\n  Recommendation ID: {recommendation_id}")

    if args.no_wait:
        print(f"  Status: SUBMITTED (use --no-wait was set)")
        print(f"  Check later with: aws bedrock-agentcore get-recommendation --recommendation-id {recommendation_id}")
        result = response
    else:
        result = poll_recommendation(client, recommendation_id)

    if result:
        # Save full result
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)

        print(f"\n\n{'─' * 60}")
        print(f"  RESULT")
        print(f"{'─' * 60}")

        status = result.get("status", "UNKNOWN")
        print(f"  Status: {status}")

        if status == "COMPLETED":
            # Extract the recommended prompt/descriptions
            recommended = result.get("recommendedConfiguration", {})
            explanation = result.get("explanation", "No explanation provided")

            print(f"\n  Explanation:")
            print(f"  {explanation[:500]}")

            if recommended:
                print(f"\n  Recommended configuration saved to: {args.output}")
                print(f"  Review the changes before applying.")

        elif status == "FAILED":
            error = result.get("failureReason", "Unknown error")
            print(f"  Failure: {error}")

    print(f"\n{'=' * 60}\n")


if __name__ == "__main__":
    main()
