from strands import Agent
from strands.models import BedrockModel
from typing import Dict, Any
import os
import json
import logging
import urllib.request
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

# Configure logging
logger = logging.getLogger("critic")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
    logger.addHandler(handler)


def create_critic_agent() -> Agent:
    """Create a critic agent that evaluates the quality of other agents' responses."""
    system_prompt = """You are a Critic agent that evaluates the quality of responses from other AI agents.

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

    Evaluate based on:
    1. Accuracy - Are the facts correct?
    2. Completeness - Does it fully address the question?
    3. Structure - Is it well-organized and clear?
    4. Depth - Does it provide sufficient detail and examples?
    5. Sources - Does it cite references when making specific claims?

    IMPORTANT: Always output valid JSON. Nothing else."""

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

    return Agent(model=model, system_prompt=system_prompt, name="CriticAgent")


@app.entrypoint
async def invoke(payload=None, context=None):
    """Main entrypoint for critic agent"""
    try:
        query = payload.get("prompt", "") if payload else ""
        parent_session = payload.get("_parent_session_id", "") if payload else ""
        if parent_session:
            logger.info(f"SESSION_CORRELATION | parent_session={parent_session} | agent=critic")

        if not query:
            return {"status": "error", "agent": "critic", "error": "No content to evaluate"}

        agent = create_critic_agent()
        response = agent(query)

        response_text = response.message["content"][0]["text"]

        # Try to parse the JSON response from the critic
        try:
            # Find JSON in the response (in case there's extra text around it)
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                evaluation = json.loads(response_text[json_start:json_end])
            else:
                evaluation = {"raw_response": response_text}
        except json.JSONDecodeError:
            evaluation = {"raw_response": response_text}

        return {
            "status": "success",
            "agent": "critic",
            "evaluation": evaluation,
            "response": response_text,
        }

    except Exception as e:
        return {"status": "error", "agent": "critic", "error": str(e)}


if __name__ == "__main__":
    app.run()
