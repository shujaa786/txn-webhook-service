import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import time
from fastapi import Request
from . import db, worker

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    task = asyncio.create_task(worker.run())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await db.close()


app = FastAPI(title="Transaction Webhook Service", lifespan=lifespan)


@app.middleware("http")
async def add_process_time(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time-Ms"] = (
        f"{(time.perf_counter() - start) * 1000:.1f}"
    )
    return response


class TransactionWebhook(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=128)
    source_account: str = Field(min_length=1, max_length=128)
    destination_account: str = Field(min_length=1, max_length=128)
    amount: Decimal = Field(gt=0, max_digits=16, decimal_places=4)
    currency: str = Field(min_length=3, max_length=3)


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


@app.get("/")
async def health():
    return {"status": "HEALTHY", "current_time": _iso(datetime.now(timezone.utc))}


@app.post("/v1/webhooks/transactions", status_code=202)
async def receive_webhook(payload: TransactionWebhook):
    try:
        created = await db.insert_transaction(payload)
    except Exception:
        # Not durably stored -> tell the sender to retry rather than silently dropping it.
        log.exception("failed to persist webhook %s", payload.transaction_id)
        return JSONResponse(
            {"status": "unavailable"}, status_code=503, headers={"Retry-After": "5"}
        )
    return {"status": "accepted", "duplicate": not created}


@app.get("/v1/transactions/{transaction_id}")
async def get_transaction(transaction_id: str):
    row = await db.get_transaction(transaction_id)
    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return [
        {
            "transaction_id": row["transaction_id"],
            "source_account": row["source_account"],
            "destination_account": row["destination_account"],
            "amount": float(row["amount"]),
            "currency": row["currency"],
            "status": row["status"],
            "created_at": _iso(row["created_at"]),
            "processed_at": _iso(row["processed_at"]),
        }
    ]
