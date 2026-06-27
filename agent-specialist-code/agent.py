from strands import Agent, tool
from strands.models import BedrockModel
from typing import Dict, Any, List
import os
import json
import logging
import urllib.request
import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

# Configure logging
logger = logging.getLogger("specialist")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
    logger.addHandler(handler)


def get_tavily_api_key():
    """Retrieve Tavily API key from Secrets Manager (or fall back to env var)."""
    secret_arn = os.getenv("TAVILY_SECRET_ARN", "")
    if secret_arn:
        try:
            region = os.getenv("AWS_REGION", "us-east-1")
            client = boto3.client("secretsmanager", region_name=region)
            response = client.get_secret_value(SecretId=secret_arn)
            return response["SecretString"]
        except Exception as e:
            logger.warning(f"Failed to retrieve secret from Secrets Manager: {e}")
    # Fall back to direct env var (for local testing)
    return os.getenv("TAVILY_API_KEY", "")


TAVILY_API_KEY = get_tavily_api_key()


@tool
def web_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    """
    Search the web for current information using Tavily Search API.
    Use this tool when you need up-to-date information, facts about recent events,
    or information about topics not in your training data.

    Args:
        query: The search query to look up on the web
        max_results: Maximum number of results to return (default 5)

    Returns:
        Search results with titles, URLs, and content snippets
    """
    if not TAVILY_API_KEY:
        return {"status": "error", "message": "TAVILY_API_KEY not configured"}

    logger.info(f"WEB_SEARCH | query={query} | max_results={max_results}")

    try:
        payload = json.dumps({
            "query": query,
            "max_results": max_results,
            "include_answer": True,
            "search_depth": "advanced",
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {TAVILY_API_KEY}",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        results = []
        for r in data.get("results", []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", "")[:500],
            })

        answer = data.get("answer", "")

        logger.info(f"WEB_SEARCH_DONE | query={query} | results_count={len(results)}")

        return {
            "status": "success",
            "answer": answer,
            "results": results,
        }

    except Exception as e:
        logger.error(f"WEB_SEARCH_ERROR | query={query} | error={str(e)}")
        return {"status": "error", "message": str(e)}


def create_specialist_agent() -> Agent:
    """Create a specialist agent that handles specific analytical tasks"""
    system_prompt = """You are a specialist analytical agent with web search capabilities.
    You are an expert at analyzing data and providing detailed insights.
    
    IMPORTANT: You have a web_search tool available. Use it when:
    - The question is about something recent or unfamiliar to you
    - You need current/up-to-date information
    - You're not confident in your knowledge of the topic
    - The user asks about a specific product, project, or event you don't recognize
    
    Always use web_search before saying you don't know something.
    When you search, synthesize the results into a clear, comprehensive answer.
    Cite sources by including relevant URLs."""

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

    tools = [web_search] if TAVILY_API_KEY else []

    return Agent(model=model, tools=tools, system_prompt=system_prompt, name="SpecialistAgent")


@app.entrypoint
async def invoke(payload=None, context=None):
    """Main entrypoint for specialist agent"""
    try:
        query = payload.get("prompt", "Hello") if payload else "Hello"
        parent_session = payload.get("_parent_session_id", "") if payload else ""
        if parent_session:
            logger.info(f"SESSION_CORRELATION | parent_session={parent_session} | agent=specialist")

        agent = create_specialist_agent()
        response = agent(query)

        return {
            "status": "success",
            "agent": "specialist",
            "response": response.message["content"][0]["text"],
        }

    except Exception as e:
        return {"status": "error", "agent": "specialist", "error": str(e)}


if __name__ == "__main__":
    app.run()
