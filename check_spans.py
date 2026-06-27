"""Quick check: do failed sessions have spans in aws/spans?"""
import boto3, time
from datetime import datetime, timedelta, timezone

client = boto3.client('logs', region_name='us-east-1')
now = datetime.now(timezone.utc)
start = int((now - timedelta(hours=24)).timestamp())
end = int(now.timestamp())

failed_sessions = [
    'c3343e77-4e3c-48cb-a93a-87ad03643981',
    '864dc2ce-7287-40bc-aa5b-dc28046661b2',
    '8ce11254-555b-448b-9110-cc6c87472b43',
]

print("Checking if failed sessions have spans in aws/spans...\n")

for sid in failed_sessions:
    qid = client.start_query(
        logGroupName='aws/spans',
        startTime=start, endTime=end,
        queryString=f'fields @timestamp | filter @message like "{sid}" | stats count()'
    )['queryId']
    time.sleep(3)
    r = client.get_query_results(queryId=qid)
    count = r['results'][0][0]['value'] if r['results'] else '0'
    print(f'  Session {sid[:16]}...: {count} spans in aws/spans')

print("\nIf counts are 0, the spans aren't reaching aws/spans (tracing not enabled or OTEL exporter issue).")
