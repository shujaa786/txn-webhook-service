"""Background worker: polls Postgres for claimable transactions and processes each one.

The database *is* the queue, so nothing is lost if the process restarts: unfinished rows stay
in PROCESSING and are re-claimed once their lock expires.
"""
import asyncio
import logging

from . import config, db

log = logging.getLogger("worker")


async def _process(transaction_id: str) -> None:
    # Simulates calling external APIs (fraud check, ledger, bank, ...).
    await asyncio.sleep(config.PROCESSING_DELAY_SECONDS)
    await db.mark_processed(transaction_id)
    log.info("processed %s", transaction_id)


async def run() -> None:
    in_flight: dict[str, asyncio.Task] = {}
    ticks = 0
    try:
        while True:
            try:
                free = config.MAX_CONCURRENCY - len(in_flight)
                claimed = await db.claim_batch(min(free, 500)) if free > 0 else []
                for tid in claimed:
                    task = asyncio.create_task(_process(tid))
                    in_flight[tid] = task
                    task.add_done_callback(lambda t, tid=tid: _done(in_flight, tid, t))
                ticks += 1
                if ticks % 30 == 0:
                    await db.fail_exhausted()
                if not claimed:
                    await asyncio.sleep(config.POLL_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("worker loop error; retrying")
                await asyncio.sleep(2)
    finally:
        # Graceful shutdown: stop tasks and hand claims back so they're retried immediately.
        for t in in_flight.values():
            t.cancel()
        try:
            await db.release(list(in_flight))
        except Exception:
            log.warning("could not release claims on shutdown; they will expire on their own")


def _done(in_flight: dict, tid: str, task: asyncio.Task) -> None:
    in_flight.pop(tid, None)
    if not task.cancelled() and task.exception():
        # Leave row in PROCESSING; its lock expires and it gets retried (up to MAX_ATTEMPTS).
        log.error("processing failed for %s: %r", tid, task.exception())
