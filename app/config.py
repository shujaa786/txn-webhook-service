import os

DATABASE_URL = os.environ.get("DATABASE_URL", "")
PROCESSING_DELAY_SECONDS = float(os.getenv("PROCESSING_DELAY_SECONDS", "30"))
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "2000"))
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "5"))
POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "1"))
# A claim older than this is considered abandoned (worker crashed) and is re-claimed.
LOCK_TIMEOUT_SECONDS = float(os.getenv("LOCK_TIMEOUT_SECONDS", str(PROCESSING_DELAY_SECONDS + 60)))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
