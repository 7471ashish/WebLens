import httpx
import time
import json

base_url = "http://127.0.0.1:8001"
target_url = "https://python.org"

print(f">>> Submitting audit for {target_url}...")
resp = httpx.post(
    f"{base_url}/audits",
    json={"url": target_url, "groq_api_key": "placeholder_or_offline_key"},
    timeout=10.0,
)

print(f"Status: {resp.status_code}, Response: {resp.json()}")
job_id = resp.json()["job_id"]

print(f">>> Polling job {job_id}...")
for _ in range(60):
    time.sleep(2)
    poll_resp = httpx.get(f"{base_url}/audits/{job_id}", timeout=10.0)
    job = poll_resp.json()
    status = job.get("status")
    progress = job.get("progress") or {}
    print(f"[{status.upper()}] Stage: {progress.get('stage')}, Percent: {progress.get('percent')}% - {progress.get('message')}")

    if status == "done":
        result = job.get("result", {})
        print("\n" + "=" * 50)
        print("AUDIT SUCCEEDED ON REAL WEBSITE!")
        print("Site:", result.get("site"))
        print("Summary:", json.dumps(result.get("summary"), indent=2))
        print(f"Total Findings Count: {len(result.get('findings', []))}")
        print("Coverage:", json.dumps(result.get("coverage"), indent=2))
        print("Meta:", json.dumps(result.get("meta"), indent=2))
        print("\nSample Findings:")
        for f in result.get("findings", [])[:5]:
            print(f"  - [{f.get('severity').upper()}] {f.get('id')}: {f.get('title')}")
            print(f"    Tier: {f.get('evidence_tier')}")
            print(f"    Action: {f.get('suggested_action', {}).get('summary')}")
        print("=" * 50)
        break
    elif status == "failed":
        print("AUDIT FAILED:", job.get("error"))
        break