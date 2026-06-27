from strands import Agent, tool
from strands.models import BedrockModel
from typing import Dict, Any
import os
import json
import logging
import urllib.request
import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

# Configure logging
logger = logging.getLogger("factchecker")
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
    return os.getenv("TAVILY_API_KEY", "")


TAVILY_API_KEY = get_tavily_api_key()


@tool
def web_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    """
    Search the web to verify facts and find evidence for or against a claim.
    Use this tool when you need to verify a claim against current sources,
    or when the claim involves recent events or specific details you're unsure about.

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


def create_factchecker_agent() -> Agent:
    """Create a fact checker agent that evaluates claims and statements"""
    system_prompt = """You are a fact-checking agent specialized in evaluating claims.
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

    return Agent(model=model, tools=tools, system_prompt=system_prompt, name="FactCheckerAgent")


@app.entrypoint
async def invoke(payload=None, context=None):
    """Main entrypoint for fact checker agent"""
    try:
        query = payload.get("prompt", "The sky is blue.") if payload else "The sky is blue."
        parent_session = payload.get("_parent_session_id", "") if payload else ""
        if parent_session:
            logger.info(f"SESSION_CORRELATION | parent_session={parent_session} | agent=factchecker")

        agent = create_factchecker_agent()
        response = agent(query)

        return {
            "status": "success",
            "agent": "factchecker",
            "response": response.message["content"][0]["text"],
        }

    except Exception as e:
        return {"status": "error", "agent": "factchecker", "error": str(e)}


if __name__ == "__main__":
    app.run()
