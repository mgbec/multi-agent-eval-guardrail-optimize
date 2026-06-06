from strands import Agent, tool
from strands.models import BedrockModel
from strands.telemetry import Tracer
from typing import Dict, Any
import boto3
import json
import os
import logging
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

# Initialize Strands OTEL tracer for GenAI semantic conventions
# This emits spans for LLM calls, tool usage, and agent reasoning
tracer = Tracer()

# Configure logging for A2A visibility
logger = logging.getLogger("orchestrator")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
    logger.addHandler(handler)

# Environment variable for Specialist Agent ARN (required - set by Terraform)
SPECIALIST_ARN = os.getenv("SPECIALIST_ARN")
if not SPECIALIST_ARN:
    raise EnvironmentError("SPECIALIST_ARN environment variable is required")

# Environment variable for Fact Checker Agent ARN (required - set by Terraform)
FACTCHECKER_ARN = os.getenv("FACTCHECKER_ARN")
if not FACTCHECKER_ARN:
    raise EnvironmentError("FACTCHECKER_ARN environment variable is required")


def invoke_agent_runtime(agent_arn: str, agent_name: str, query: str) -> str:
    """Generic helper to invoke any agent runtime using boto3"""
    try:
        region = os.getenv("AWS_REGION")
        if not region:
            raise EnvironmentError("AWS_REGION environment variable is required")
        agentcore_client = boto3.client("bedrock-agentcore", region_name=region)

        logger.info(f"A2A_CALL_START | target={agent_name} | arn={agent_arn} | query_length={len(query)}")
        logger.info(f"A2A_CALL_PAYLOAD | agent={agent_name} | query={query[:500]}")

        import time
        start_time = time.time()

        response = agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            qualifier="DEFAULT",
            payload=json.dumps({"prompt": query}),
        )

        # Handle streaming response (text/event-stream)
        if "text/event-stream" in response.get("contentType", ""):
            result = ""
            for line in response["response"].iter_lines(chunk_size=10):
                if line:
                    line = line.decode("utf-8")
                    if line.startswith("data: "):
                        line = line[6:]
                    result += line
            elapsed = (time.time() - start_time) * 1000
            logger.info(f"A2A_CALL_END | agent={agent_name} | latency_ms={elapsed:.0f} | response_length={len(result)}")
            logger.info(f"A2A_CALL_RESPONSE | agent={agent_name} | response={result[:500]}")
            return result

        # Handle JSON response
        elif response.get("contentType") == "application/json":
            content = []
            for chunk in response.get("response", []):
                content.append(chunk.decode("utf-8"))
            response_data = json.loads("".join(content))
            elapsed = (time.time() - start_time) * 1000
            logger.info(f"A2A_CALL_END | agent={agent_name} | latency_ms={elapsed:.0f} | response_length={len(json.dumps(response_data))}")
            logger.info(f"A2A_CALL_RESPONSE | agent={agent_name} | response={json.dumps(response_data)[:500]}")
            return json.dumps(response_data)

        # Handle other response types
        else:
            response_body = response["response"].read()
            elapsed = (time.time() - start_time) * 1000
            result = response_body.decode("utf-8")
            logger.info(f"A2A_CALL_END | agent={agent_name} | latency_ms={elapsed:.0f} | response_length={len(result)}")
            logger.info(f"A2A_CALL_RESPONSE | agent={agent_name} | response={result[:500]}")
            return result

    except Exception as e:
        import traceback
        logger.error(f"A2A_CALL_ERROR | agent={agent_name} | arn={agent_arn} | error={str(e)}")
        error_details = traceback.format_exc()
        return f"Error invoking {agent_name}: {str(e)}\nDetails: {error_details}"


@tool
def call_specialist_agent(query: str) -> Dict[str, Any]:
    """
    Call the specialist agent for detailed analysis or complex tasks.
    Use this tool when you need expert analysis, detailed explanations,
    or in-depth information on a topic.

    Args:
        query: The question or task to send to the specialist agent

    Returns:
        The specialist agent's response
    """
    result = invoke_agent_runtime(SPECIALIST_ARN, "specialist", query)
    return {"status": "success", "content": [{"text": result}]}


@tool
def call_factchecker_agent(claim: str) -> Dict[str, Any]:
    """
    Call the fact checker agent to verify a claim or statement.
    Use this tool when you need to verify facts, check accuracy of statements,
    or assess the truthfulness of a claim.

    Args:
        claim: The statement or claim to fact-check

    Returns:
        The fact checker agent's verdict with confidence level
    """
    result = invoke_agent_runtime(FACTCHECKER_ARN, "factchecker", claim)
    return {"status": "success", "content": [{"text": result}]}


def create_orchestrator_agent() -> Agent:
    """Create the orchestrator agent with tools to call specialist and fact checker"""
    system_prompt = """You are an orchestrator agent that coordinates between specialized agents.
    You have two tools available:

    1. call_specialist_agent - For detailed analysis, explanations, and complex research tasks
    2. call_factchecker_agent - For verifying claims, checking facts, and assessing truthfulness

    Routing guidelines:
    - For questions requiring detailed analysis or explanation → use call_specialist_agent
    - For verifying specific claims or statements → use call_factchecker_agent
    - For complex queries that involve both analysis AND fact verification → use BOTH tools
      (first get the analysis from the specialist, then verify key claims with the fact checker)
    - For simple greetings or basic questions → handle directly yourself

    When using both tools, synthesize their responses into a coherent answer."""

    model_id = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    model = BedrockModel(model_id=model_id)

    return Agent(
        model=model,
        tools=[call_specialist_agent, call_factchecker_agent],
        system_prompt=system_prompt,
        name="OrchestratorAgent",
        tracer=tracer,
    )


@app.entrypoint
async def invoke(payload=None):
    """Main entrypoint for orchestrator agent"""
    try:
        # Get the query from payload
        query = (
            payload.get("prompt", "Hello, how are you?")
            if payload
            else "Hello, how are you?"
        )

        # Create and use the orchestrator agent
        agent = create_orchestrator_agent()
        response = agent(query)

        return {
            "status": "success",
            "agent": "orchestrator",
            "response": response.message["content"][0]["text"],
        }

    except Exception as e:
        return {"status": "error", "agent": "orchestrator", "error": str(e)}


if __name__ == "__main__":
    app.run()
