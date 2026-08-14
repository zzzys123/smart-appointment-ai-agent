"""Redis integration settings.

Redis is optional so a fresh clone can still run without external services.  Set
``REDIS_ENABLED=true`` to enable shared sessions, rate limiting and distributed
appointment locks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv


load_dotenv()


def _as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RedisConfig:
    enabled: bool = _as_bool(os.getenv("REDIS_ENABLED", "false"))
    url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    key_prefix: str = os.getenv("REDIS_KEY_PREFIX", "smart-appointment")
    session_ttl_seconds: int = int(os.getenv("REDIS_SESSION_TTL_SECONDS", "3600"))
    idempotency_ttl_seconds: int = int(
        os.getenv("REDIS_IDEMPOTENCY_TTL_SECONDS", "86400")
    )
    appointment_lock_seconds: int = int(
        os.getenv("REDIS_APPOINTMENT_LOCK_SECONDS", "15")
    )
    appointment_lock_wait_seconds: int = int(
        os.getenv("REDIS_APPOINTMENT_LOCK_WAIT_SECONDS", "5")
    )
    chat_lock_seconds: int = int(os.getenv("REDIS_CHAT_LOCK_SECONDS", "120"))
    chat_lock_wait_seconds: int = int(os.getenv("REDIS_CHAT_LOCK_WAIT_SECONDS", "2"))
    rate_limit_requests: int = int(os.getenv("REDIS_RATE_LIMIT_REQUESTS", "10"))
    rate_limit_window_seconds: int = int(
        os.getenv("REDIS_RATE_LIMIT_WINDOW_SECONDS", "60")
    )


redis_config = RedisConfig()
