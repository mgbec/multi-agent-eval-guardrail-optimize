"""Check the status of a recommendation job."""
import boto3
import json
import sys

REGION = "us-east-1"
REC_ID = "orchestrator-prompt-opt-1782573036-9802E0B9F2"

if len(sys.argv) > 1:
    REC_ID = sys.argv[1]

client = boto3.client("bedrock-agentcore", region_name=REGION)
r = client.get_recommendation(recommendationId=REC_ID)

print(f"Recommendation: {REC_ID}")
print(f"Status: {r['status']}")
print(f"Created: {r.get('createdAt', '')}")
print(f"Updated: {r.get('updatedAt', '')}")

if r["status"] in ("COMPLETED", "SUCCEEDED"):
    result = r.get("recommendationResult", {})
    sp = result.get("systemPromptRecommendationResult", {})
    if sp.get("recommendedSystemPrompt"):
        prompt = sp["recommendedSystemPrompt"]
        print(f"\nRecommended prompt ({len(prompt)} chars):")
        print("-" * 60)
        print(prompt)
        print("-" * 60)
        # Save to file
        with open("recommended_prompt.txt", "w", encoding="utf-8") as f:
            f.write(prompt)
        print("\nSaved to: recommended_prompt.txt")
    elif sp.get("errorMessage"):
        print(f"\nError: {sp['errorMessage']}")

elif r["status"] == "FAILED":
    result = r.get("recommendationResult", {})
    sp = result.get("systemPromptRecommendationResult", {})
    td = result.get("toolDescriptionRecommendationResult", {})
    err = sp.get("errorMessage", "") or td.get("errorMessage", "")
    print(f"\nFailed: {err}")

else:
    print("\nStill processing... run this script again in a few minutes.")
