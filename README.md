# Transaction Webhook Service

Receives payment-processor webhooks, acknowledges instantly (`202`), and processes each transaction
in the background (30s simulated external call), with idempotency and crash-safe persistence.

**Live URL:** `https://<your-service>.onrender.com`  <!-- replace after deploying -->

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Health check → `{"status":"HEALTHY","current_time":"..."}` |
| POST | `/v1/webhooks/transactions` | Accepts a webhook, returns `202` |
| GET | `/v1/transactions/{transaction_id}` | Returns `[ {..., "status": "PROCESSING" \| "PROCESSED", "processed_at": ...} ]` (404 if unknown) |

## Run locally

```bash
docker compose up --build        # API on http://localhost:8000, Postgres included
python scripts/smoke_test.py     # sends 3 duplicate webhooks, waits ~30s, verifies a single PROCESSED txn
```

Without Docker: start any Postgres, `pip install -r requirements.txt`,
`export DATABASE_URL=postgresql://...`, `uvicorn app.main:app --reload`.

Manual test:
```bash
curl -i -X POST $URL/v1/webhooks/transactions -H 'Content-Type: application/json' -d '{
  "transaction_id":"txn_abc123def456","source_account":"acc_user_789",
  "destination_account":"acc_merchant_456","amount":1500,"currency":"INR"}'
curl $URL/v1/transactions/txn_abc123def456
```

## Deploy (Render, free tier)

1. Push this repo to GitHub (public).
2. Render dashboard → **New → Blueprint** → select the repo. `render.yaml` provisions the web service + Postgres and wires `DATABASE_URL`.
3. Once live, run `python scripts/smoke_test.py https://<your-service>.onrender.com`.

Works on any container host (Railway, Fly.io, Cloud Run) with any Postgres (Neon, Supabase, RDS): just set `DATABASE_URL`.
> Free Render services sleep after ~15 min idle; the first request wakes them (can exceed 500ms). Ping `/` before testing, or use a paid instance / UptimeRobot.

## Technical choices

- **FastAPI + asyncpg**: fully async; the webhook handler does exactly one indexed INSERT, so it responds in a few ms regardless of how many transactions are processing.
- **Postgres as both store and queue**: no Redis/Celery to operate. A transaction is durably saved *before* we return 202, so it can't be lost. If the DB write fails we return `503` + `Retry-After` so the processor retries instead of the webhook vanishing.
- **Idempotency**: `transaction_id` is the primary key and the insert uses `ON CONFLICT DO NOTHING`. This is race-safe across concurrent requests and instances; duplicates get a normal `202`.
- **Background worker** (in-process asyncio task): polls and claims rows with `UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED)`, then runs each in its own task (`sleep(30)` → mark `PROCESSED` with `processed_at`). Thousands of concurrent 30s waits cost almost nothing.
- **Reliability**: claims carry a `locked_at` timestamp. If the process crashes/redeploys mid-processing, the lock expires (delay + 60s) and the row is re-claimed. Graceful shutdown releases claims immediately. Retries are capped at `MAX_ATTEMPTS`, after which status becomes `FAILED`. `SKIP LOCKED` means you can scale to multiple instances with no double-processing; the `status='PROCESSING'` guard on the final update makes completion idempotent.
- **Money**: stored as `NUMERIC`, not float.

## Trade-offs / next steps
Polling (1s) instead of LISTEN/NOTIFY; for much higher volume, split the worker into its own service (same image, different entrypoint) or move to SQS/Cloud Tasks. Add webhook signature verification (e.g. Razorpay's `X-Razorpay-Signature`) for production.
