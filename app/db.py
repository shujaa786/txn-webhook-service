import asyncpg

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id       TEXT PRIMARY KEY,           -- PK enforces idempotency
    source_account       TEXT NOT NULL,
    destination_account  TEXT NOT NULL,
    amount               NUMERIC(20, 4) NOT NULL,
    currency             CHAR(3) NOT NULL,
    status               TEXT NOT NULL DEFAULT 'PROCESSING',  -- PROCESSING | PROCESSED | FAILED
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at         TIMESTAMPTZ,
    locked_at            TIMESTAMPTZ,                -- set when a worker claims the row
    attempts             INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_transactions_pending
    ON transactions (created_at) WHERE status = 'PROCESSING';
"""

pool: asyncpg.Pool | None = None


async def init() -> None:
    global pool
    pool = await asyncpg.create_pool(
        config.DATABASE_URL, min_size=1, max_size=config.DB_POOL_MAX, command_timeout=10
    )
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)


async def close() -> None:
    if pool:
        await pool.close()


async def insert_transaction(t) -> bool:
    """Insert if new. Returns True if inserted, False if it was a duplicate."""
    row = await pool.fetchrow(
        """
        INSERT INTO transactions
            (transaction_id, source_account, destination_account, amount, currency)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (transaction_id) DO NOTHING
        RETURNING transaction_id
        """,
        t.transaction_id, t.source_account, t.destination_account, t.amount, t.currency.upper(),
    )
    return row is not None


async def get_transaction(transaction_id: str):
    return await pool.fetchrow(
        """
        SELECT transaction_id, source_account, destination_account, amount,
               currency, status, created_at, processed_at
        FROM transactions WHERE transaction_id = $1
        """,
        transaction_id,
    )


async def claim_batch(limit: int) -> list[str]:
    """Atomically claim up to `limit` unprocessed (or abandoned) rows.
    SKIP LOCKED makes this safe with multiple app instances."""
    rows = await pool.fetch(
        """
        UPDATE transactions SET locked_at = now(), attempts = attempts + 1
        WHERE transaction_id IN (
            SELECT transaction_id FROM transactions
            WHERE status = 'PROCESSING'
              AND attempts < $2
              AND (locked_at IS NULL OR locked_at < now() - make_interval(secs => $3))
            ORDER BY created_at
            LIMIT $1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING transaction_id
        """,
        limit, config.MAX_ATTEMPTS, config.LOCK_TIMEOUT_SECONDS,
    )
    return [r["transaction_id"] for r in rows]


async def mark_processed(transaction_id: str) -> None:
    await pool.execute(
        """
        UPDATE transactions SET status = 'PROCESSED', processed_at = now(), locked_at = NULL
        WHERE transaction_id = $1 AND status = 'PROCESSING'
        """,
        transaction_id,
    )


async def release(transaction_ids: list[str]) -> None:
    """Give claims back (graceful shutdown / transient error) so another worker can retry."""
    if transaction_ids:
        await pool.execute(
            "UPDATE transactions SET locked_at = NULL WHERE transaction_id = ANY($1) AND status = 'PROCESSING'",
            transaction_ids,
        )


async def fail_exhausted() -> None:
    await pool.execute(
        """
        UPDATE transactions SET status = 'FAILED', locked_at = NULL
        WHERE status = 'PROCESSING' AND attempts >= $1
          AND (locked_at IS NULL OR locked_at < now() - make_interval(secs => $2))
        """,
        config.MAX_ATTEMPTS, config.LOCK_TIMEOUT_SECONDS,
    )
