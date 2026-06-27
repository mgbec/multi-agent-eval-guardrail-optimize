"""Debug: find session IDs in aws/spans using different field paths."""
import boto3
import time
from datetime import datetime, timedelta, timezone

client = boto3.client("logs", region_name="us-east-1")
now = datetime.now(timezone.utc)
start = int((now - timedelta(days=1)).timestamp())
end = int(now.timestamp())

queries = {
    "Total records": "fields @timestamp | stats count()",
    "session.id (top-level)": "fields `session.id` as sid | filter ispresent(`session.id`) | stats count() by sid | limit 5",
    "attributes.session.id": "fields attributes.`session.id` as sid | filter ispresent(attributes.`session.id`) | stats count() by sid | limit 5",
    "grep session in raw message": "fields @message | filter @message like /session/ | limit 1",
}

print(f"Searching aws/spans (last 24h)\n")

for label, query in queries.items():
    qid = client.start_query(logGroupName="aws/spans", startTime=start, endTime=end, queryString=query)["queryId"]
    time.sleep(4)
    r = client.get_query_results(queryId=qid)
    print(f"{label}:")
    print(f"  Rows returned: {len(r['results'])}")
    if r["results"]:
        for row in r["results"][:3]:
            fields = {f["field"]: f["value"][:80] for f in row}
            print(f"  {fields}")
    print()
