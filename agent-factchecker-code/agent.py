from strands import Agent
from strands.models import BedrockModel
from strands.telemetry import Tracer
import os
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

# Initialize Strands OTEL tracer for GenAI semantic conventions
tracer = Tracer()


def create_factchecker_agent() -> Agent:
    """Create a fact checker agent that evaluates claims and statements"""
    system_prompt = """You are a fact-checking agent specialized in evaluating claims.
    When given a statement or claim, you must:
    1. Identify the core claim being made
    2. Evaluate the claim based on your knowledge
    3. Provide a confidence assessment: HIGH, MEDIUM, or LOW
    4. Explain your reasoning briefly

    Always respond in this format:
    CLAIM: [restate the claim]
    VERDICT: [TRUE / FALSE / PARTIALLY TRUE / UNVERIFIABLE]
    CONFIDENCE: [HIGH / MEDIUM / LOW]
    REASONING: [brief explanation]

    Be concise and precise. Focus on factual accuracy, not opinions."""

    model_id = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    model = BedrockModel(model_id=model_id)

    return Agent(model=model, system_prompt=system_prompt, name="FactCheckerAgent", tracer=tracer)


@app.entrypoint
async def invoke(payload=None):
    """Main entrypoint for fact checker agent"""
    try:
        query = payload.get("prompt", "The sky is blue.") if payload else "The sky is blue."

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
