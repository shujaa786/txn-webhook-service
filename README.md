# Transaction Webhook Service

Receives payment-processor webhooks, acknowledges instantly (`202`), and processes each transaction
in the background (30s simulated external call), with idempotency and crash-safe persistence.

**Live URL:** https://txn-webhook-service-kr2w.onrender.com/
**ARCHITECTURE  [`ARCHITECTURE.md`](ARCHITECTURE.md)
## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Health check → `{"status":"HEALTHY","current_time":"..."}` |
| POST | `/v1/webhooks/transactions` | Accepts a webhook, returns `202` |
| GET | `/v1/transactions/{transaction_id}` | Returns `[ {..., "status": "PROCESSING" \| "PROCESSED", "processed_at": ...} ]` (404 if unknown) |

## Test the live deployment

```bash
python scripts/smoke_test.py https://txn-webhook-service-kr2w.onrender.com
```

The script sends the same webhook 3 times (checks `202` + response time), confirms only one transaction exists, then waits ~30s and verifies it becomes `PROCESSED`.

Or manually:

```bash
URL=https://txn-webhook-service-kr2w.onrender.com

curl -i -X POST $URL/v1/webhooks/transactions \
  -H 'Content-Type: application/json' \
  -d '{"transaction_id":"txn_abc123def456","source_account":"acc_user_789","destination_account":"acc_merchant_456","amount":1500,"currency":"INR"}'

curl $URL/v1/transactions/txn_abc123def456
```

Status shows `PROCESSING` first, then `PROCESSED` (with `processed_at`) after ~30s.

> Hosted on Render free tier (Singapore region). The service is pinged every 5 minutes (UptimeRobot) to avoid cold starts; if it has slept, the first request may take a few seconds.

## Run locally

```bash
docker compose up --build        # API on http://localhost:8000, Postgres included
python scripts/smoke_test.py     # defaults to http://localhost:8000
```

Without Docker: start any Postgres, then

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:pass@localhost:5432/txn
uvicorn app.main:app --reload
```

Manual test against local:

```bash
URL=http://localhost:8000
curl -i -X POST $URL/v1/webhooks/transactions -H 'Content-Type: application/json' \
  -d '{"transaction_id":"txn_abc123def456","source_account":"acc_user_789","destination_account":"acc_merchant_456","amount":1500,"currency":"INR"}'
curl $URL/v1/transactions/txn_abc123def456
```

## Deploy (Render, free tier)

1. Push this repo to GitHub (public).
2. Render dashboard → **New → Blueprint** → select the repo. `render.yaml` provisions the web service + Postgres (both in Singapore) and wires `DATABASE_URL`.
3. Once live, run `python scripts/smoke_test.py https://txn-webhook-service-kr2w.onrender.com`.

Works on any container host (Railway, Fly.io, Cloud Run) with any Postgres (Neon, Supabase, RDS): just set `DATABASE_URL`.

## Technical choices

- **FastAPI + asyncpg**: fully async; the webhook handler does exactly one indexed INSERT, so it responds in a few ms regardless of how many transactions are processing.
- **Latency**: server-side handling is a single DB insert. Any measured time beyond that is network round-trip (TCP + TLS), which is why the service is deployed in a region close to the expected callers.
- **Postgres as both store and queue**: no Redis/Celery to operate. A transaction is durably saved *before* we return 202, so it can't be lost. If the DB write fails we return `503` + `Retry-After` so the processor retries instead of the webhook vanishing.
- **Idempotency**: `transaction_id` is the primary key and the insert uses `ON CONFLICT DO NOTHING`. This is race-safe across concurrent requests and instances; duplicates get a normal `202`.
- **Background worker** (in-process asyncio task): polls and claims rows with `UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED)`, then runs each in its own task (`sleep(30)` → mark `PROCESSED` with `processed_at`). Thousands of concurrent 30s waits cost almost nothing.
- **Reliability**: claims carry a `locked_at` timestamp. If the process crashes or redeploys mid-processing, the lock expires (delay + 60s) and the row is re-claimed. Graceful shutdown releases claims immediately. Retries are capped at `MAX_ATTEMPTS`, after which status becomes `FAILED`. `SKIP LOCKED` means you can scale to multiple instances with no double-processing; the `status='PROCESSING'` guard on the final update makes completion idempotent.
- **Money**: stored as `NUMERIC`, not float.

## Trade-offs / next steps

Polling (1s) instead of LISTEN/NOTIFY; for much higher volume, split the worker into its own service (same image, different entrypoint) or move to SQS/Cloud Tasks. Add webhook signature verification (e.g. Razorpay's `X-Razorpay-Signature`) for production.
