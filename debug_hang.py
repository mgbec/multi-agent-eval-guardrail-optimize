"""Debug: check recent orchestrator logs for the hang."""
import boto3
import time
from datetime import datetime, timedelta, timezone

client = boto3.client("logs", region_name="us-east-1")
now = datetime.now(timezone.utc)
start = int((now - timedelta(minutes=20)).timestamp())
end = int(now.timestamp())

LOG_GROUP = "/aws/bedrock-agentcore/runtimes/agentcore_multi_agent_OrchestratorAgent-k2IPfkBr3t-DEFAULT"

qid = client.start_query(
    logGroupName=LOG_GROUP,
    startTime=start, endTime=end,
    queryString="fields @timestamp, @message | filter @message like /A2A_CALL|ERROR|SESSION|PROPAGATE/ | sort @timestamp desc | limit 30"
)["queryId"]
time.sleep(5)
r = client.get_query_results(queryId=qid)
print(f"Log group: {LOG_GROUP}")
print(f"Found {len(r.get('results', []))} entries\n")
for row in r.get("results", []):
    ts = ""
    msg = ""
    for f in row:
        if f["field"] == "@timestamp":
            ts = f["value"]
        if f["field"] == "@message":
            msg = f["value"][:250]
    print(f"[{ts}] {msg}")
    print()
