"""End-to-end check against a running service.
Usage: python scripts/smoke_test.py https://your-app.onrender.com
"""
import json, sys, time, uuid, urllib.request, urllib.error

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null"), time.time() - start
    except urllib.error.HTTPError as e:
        return e.code, None, time.time() - start


print("health:", call("GET", "/")[:2])
txn = {"transaction_id": f"txn_{uuid.uuid4().hex[:12]}", "source_account": "acc_user_789",
       "destination_account": "acc_merchant_456", "amount": 1500, "currency": "INR"}

for i in range(3):  # duplicates
    status, _, dt = call("POST", "/v1/webhooks/transactions", txn)
    print(f"webhook #{i+1}: {status} in {dt*1000:.0f}ms")
    assert status == 202 and dt < 0.5, "must be 202 and <500ms"

status, body, _ = call("GET", f"/v1/transactions/{txn['transaction_id']}")
print("immediately:", body[0]["status"]); assert len(body) == 1

print("waiting for processing (~30s)...")
for _ in range(60):
    time.sleep(1)
    _, body, _ = call("GET", f"/v1/transactions/{txn['transaction_id']}")
    if body[0]["status"] == "PROCESSED":
        print("PROCESSED:", body[0]); break
else:
    sys.exit("FAILED: not processed in time")
print("OK")
