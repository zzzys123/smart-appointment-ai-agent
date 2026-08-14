"""Optional Redis-backed coordination primitives.

The service deliberately degrades to process-local locks when Redis is disabled
or unavailable. Persistent business data always remains in SQL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator

from config.redis_config import RedisConfig, redis_config

try:
    import redis
    import redis.asyncio as async_redis
except ImportError:  # pragma: no cover - exercised in environments without extras
    redis = None
    async_redis = None


logger = logging.getLogger(__name__)


class RedisService:
    def __init__(self, config: RedisConfig = redis_config):
        self.config = config
        self._async_client = None
        self._sync_client = None
        self._available = False
        self._next_connect_attempt = 0.0
        self._connect_lock = asyncio.Lock()
        self._local_locks: dict[str, threading.Lock] = {}
        self._local_async_locks: dict[str, asyncio.Lock] = {}
        self._local_idempotency_keys: dict[str, float] = {}
        self._local_locks_guard = threading.Lock()

    @property
    def available(self) -> bool:
        return self._available

    def key(self, *parts: object) -> str:
        clean_parts = [str(part).replace(":", "_") for part in parts]
        return ":".join([self.config.key_prefix, *clean_parts])

    async def connect(self) -> bool:
        if not self.config.enabled:
            return False
        if async_redis is None or redis is None:
            logger.warning("Redis is enabled but the 'redis' package is not installed")
            return False
        if self._available:
            return True
        if time.monotonic() < self._next_connect_attempt:
            return False

        async with self._connect_lock:
            if self._available:
                return True
            async_client = None
            sync_client = None
            try:
                async_client = async_redis.from_url(
                    self.config.url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=1,
                    socket_timeout=2,
                )
                await async_client.ping()
                sync_client = redis.Redis.from_url(
                    self.config.url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=1,
                    socket_timeout=2,
                )
                sync_client.ping()
                self._async_client = async_client
                self._sync_client = sync_client
                self._available = True
                self._next_connect_attempt = 0.0
                logger.info("Redis coordination is enabled")
            except Exception as exc:
                logger.warning("Redis unavailable; using local fallbacks: %s", exc)
                self._available = False
                self._next_connect_attempt = time.monotonic() + 30
                if async_client is not None:
                    await async_client.aclose()
                if sync_client is not None:
                    sync_client.close()
        return self._available

    async def close(self) -> None:
        if self._async_client is not None:
            await self._async_client.aclose()
        if self._sync_client is not None:
            self._sync_client.close()
        self._async_client = None
        self._sync_client = None
        self._available = False

    async def get_session(self, session_id: str) -> dict[str, Any]:
        if not await self.connect():
            return {}
        value = await self._async_client.get(self.key("session", session_id))
        if not value:
            return {}
        try:
            result = json.loads(value)
            return result if isinstance(result, dict) else {}
        except json.JSONDecodeError:
            logger.warning("Ignoring invalid Redis session payload for %s", session_id)
            return {}

    async def save_session(self, session_id: str, state: dict[str, Any]) -> None:
        if not await self.connect():
            return
        await self._async_client.set(
            self.key("session", session_id),
            json.dumps(state, ensure_ascii=False, default=str),
            ex=self.config.session_ttl_seconds,
        )

    async def delete_session(self, session_id: str) -> None:
        if await self.connect():
            await self._async_client.delete(self.key("session", session_id))

    async def allow_chat_request(self, session_id: str) -> bool:
        """Fixed-window limit; disabled Redis means no cross-process limiting."""
        if not await self.connect():
            return True
        rate_key = self.key("rate_limit", "chat", session_id)
        count = await self._async_client.incr(rate_key)
        if int(count) == 1:
            await self._async_client.expire(
                rate_key, self.config.rate_limit_window_seconds
            )
        return int(count) <= self.config.rate_limit_requests

    @asynccontextmanager
    async def chat_lock(self, session_id: str) -> AsyncIterator[bool]:
        lock_name = self.key("lock", "chat", session_id)
        if await self.connect():
            lock = self._async_client.lock(
                lock_name,
                timeout=self.config.chat_lock_seconds,
                blocking_timeout=self.config.chat_lock_wait_seconds,
            )
            acquired = False
            try:
                acquired = bool(await lock.acquire())
                yield acquired
            finally:
                if acquired:
                    try:
                        await lock.release()
                    except Exception as exc:
                        logger.warning("Could not release Redis chat lock: %s", exc)
            return

        lock = self._local_async_locks.setdefault(lock_name, asyncio.Lock())
        acquired = False
        try:
            await asyncio.wait_for(
                lock.acquire(), timeout=self.config.chat_lock_wait_seconds
            )
            acquired = True
        except asyncio.TimeoutError:
            pass
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()

    def _local_lock(self, lock_name: str) -> threading.Lock:
        with self._local_locks_guard:
            return self._local_locks.setdefault(lock_name, threading.Lock())

    @contextmanager
    def appointment_lock(self, technician_id: int, date_key: str) -> Iterator[bool]:
        lock_name = self.key("lock", "appointment", technician_id, date_key)
        if self._available and self._sync_client is not None:
            lock = self._sync_client.lock(
                lock_name,
                timeout=self.config.appointment_lock_seconds,
                blocking_timeout=self.config.appointment_lock_wait_seconds,
            )
            acquired = False
            try:
                acquired = bool(lock.acquire())
                yield acquired
            finally:
                if acquired:
                    try:
                        lock.release()
                    except Exception as exc:
                        logger.warning("Could not release Redis appointment lock: %s", exc)
            return

        lock = self._local_lock(lock_name)
        acquired = lock.acquire(timeout=self.config.appointment_lock_wait_seconds)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()

    def is_idempotent_appointment(self, idempotency_key: str) -> bool:
        if not self._available or self._sync_client is None:
            expires_at = self._local_idempotency_keys.get(idempotency_key, 0)
            if expires_at <= time.monotonic():
                self._local_idempotency_keys.pop(idempotency_key, None)
                return False
            return True
        return bool(self._sync_client.exists(self.key("idempotency", idempotency_key)))

    def mark_idempotent_appointment(self, idempotency_key: str) -> None:
        if self._available and self._sync_client is not None:
            self._sync_client.set(
                self.key("idempotency", idempotency_key),
                "1",
                ex=self.config.idempotency_ttl_seconds,
            )
        else:
            self._local_idempotency_keys[idempotency_key] = (
                time.monotonic() + self.config.idempotency_ttl_seconds
            )


_redis_service = RedisService()


def get_redis_service() -> RedisService:
    return _redis_service
