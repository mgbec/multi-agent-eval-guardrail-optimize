from strands import Agent, tool
from strands.models import BedrockModel
from typing import Dict, Any
import boto3
import json
import os
import logging
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

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

# Environment variable for Critic Agent ARN (required - set by Terraform)
CRITIC_ARN = os.getenv("CRITIC_ARN")
if not CRITIC_ARN:
    raise EnvironmentError("CRITIC_ARN environment variable is required")


# Thread-local storage for session ID propagation to tool functions
import threading
_session_context = threading.local()


def invoke_agent_runtime(agent_arn: str, agent_name: str, query: str) -> str:
    """Generic helper to invoke any agent runtime using boto3.
    Propagates the Orchestrator's session ID to downstream agents for trace coherence."""
    try:
        region = os.getenv("AWS_REGION")
        if not region:
            raise EnvironmentError("AWS_REGION environment variable is required")
        agentcore_client = boto3.client("bedrock-agentcore", region_name=region)

        logger.info(f"A2A_CALL_START | target={agent_name} | arn={agent_arn} | query_length={len(query)}")
        logger.info(f"A2A_CALL_PAYLOAD | agent={agent_name} | query={query[:500]}")

        import time
        start_time = time.time()

        # Build invoke kwargs — propagate session ID if available
        invoke_kwargs = {
            "agentRuntimeArn": agent_arn,
            "qualifier": "DEFAULT",
            "payload": json.dumps({"prompt": query}),
        }

        # Session correlation for observability:
        # We log the parent session ID in the payload metadata so traces can be correlated,
        # but we do NOT pass runtimeSessionId to downstream agents because AgentCore uses it
        # for sticky session routing, which causes deadlocks when the Orchestrator is already
        # occupying a session and tries to call a downstream agent on the same or related session.
        session_id = getattr(_session_context, "session_id", None)
        if session_id:
            # Embed parent session in the payload so downstream agents can log it
            payload_data = json.loads(invoke_kwargs["payload"])
            payload_data["_parent_session_id"] = session_id
            invoke_kwargs["payload"] = json.dumps(payload_data)
            logger.info(f"A2A_SESSION_CONTEXT | agent={agent_name} | orchestrator_session={session_id}")

        response = agentcore_client.invoke_agent_runtime(**invoke_kwargs)

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


@tool
def call_critic_agent(content_to_evaluate: str) -> Dict[str, Any]:
    """
    Call the critic agent to evaluate the quality of a response.
    Use this tool after receiving a response from the specialist or fact checker
    to assess its quality and get improvement suggestions.

    Use the critic when:
    - You want to verify the quality of a specialist's response before presenting it
    - The question is important and deserves a quality check
    - You want to know if the response needs more detail or has gaps

    Args:
        content_to_evaluate: The response text to evaluate (include the original question for context)

    Returns:
        Quality score (1-10), verdict, strengths, weaknesses, and improvement suggestion
    """
    result = invoke_agent_runtime(CRITIC_ARN, "critic", content_to_evaluate)
    return {"status": "success", "content": [{"text": result}]}


def create_orchestrator_agent() -> Agent:
    """Create the orchestrator agent with tools to call specialist, fact checker, and critic"""
    max_retries = int(os.getenv("MAX_CRITIC_RETRIES", "2"))

    system_prompt = f"""You are an orchestrator agent that coordinates between specialized agents.
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
    - IMPORTANT: Maximum {max_retries} retries allowed. After {max_retries} retries, use the best response you have.
    - Present the final (improved) response to the user

    When using multiple tools, synthesize their responses into a coherent answer.
    Always mention if you used the critic to improve a response."""

    model_id = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    guardrail_id = os.getenv("BEDROCK_GUARDRAIL_ID", "")
    guardrail_ver = os.getenv("BEDROCK_GUARDRAIL_VER", "")

    model_kwargs = {"model_id": model_id}
    if guardrail_id and guardrail_ver:
        model_kwargs["guardrail_config"] = {
            "guardrailIdentifier": guardrail_id,
            "guardrailVersion": guardrail_ver,
        }
    model = BedrockModel(**model_kwargs)

    return Agent(
        model=model,
        tools=[call_specialist_agent, call_factchecker_agent, call_critic_agent],
        system_prompt=system_prompt,
        name="OrchestratorAgent",
    )


@app.entrypoint
async def invoke(payload=None, context=None):
    """Main entrypoint for orchestrator agent"""
    try:
        # Capture session ID for propagation to downstream A2A calls
        if context and hasattr(context, "session_id"):
            _session_context.session_id = context.session_id
            logger.info(f"SESSION_START | session_id={context.session_id}")
        else:
            _session_context.session_id = None

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
