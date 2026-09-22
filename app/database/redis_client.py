import json
import logging

import redis

from app.config import settings
from app.schemas.chat import ChatHistoryMessage

logger = logging.getLogger(__name__)

_pool: redis.ConnectionPool | None = None


def _connection_pool() -> redis.ConnectionPool:
    global _pool
    if _pool is None:
        if settings.REDIS_URL:
            _pool = redis.ConnectionPool.from_url(
                settings.REDIS_URL,
                socket_connect_timeout=settings.REDIS_TIMEOUT_SECONDS,
                socket_timeout=settings.REDIS_TIMEOUT_SECONDS,
                decode_responses=True,
            )
        else:
            _pool = redis.ConnectionPool(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD or None,
                socket_connect_timeout=settings.REDIS_TIMEOUT_SECONDS,
                socket_timeout=settings.REDIS_TIMEOUT_SECONDS,
                decode_responses=True,
            )
    return _pool


def get_redis_client() -> redis.Redis | None:
    """Return a pooled Redis client, or None when the cache is disabled.

    Callers must treat None (and any RedisError raised by the helpers below)
    as "cache unavailable" and fall back to Postgres. This function never
    raises for a bad connection - only a bug in configuration parsing would
    raise here, which should surface immediately rather than degrade silently.
    """
    if not settings.ENABLE_REDIS_CACHE:
        return None
    return redis.Redis(connection_pool=_connection_pool())


def _history_key(user_id: int, session_id: int) -> str:
    return f"foodai:chat:{user_id}:{session_id}:recent"


def _memory_key(user_id: int, session_id: int) -> str:
    return f"foodai:chat:{user_id}:{session_id}:memory"


def _document_retrieval_key(user_id: int, document_id: int) -> str:
    return f"foodai:document:{user_id}:{document_id}:retrieval"


def get_cached_history(user_id: int, session_id: int) -> list[ChatHistoryMessage] | None:
    client = get_redis_client()
    if client is None:
        return None
    try:
        raw = client.get(_history_key(user_id, session_id))
        if raw is None:
            return None
        return [ChatHistoryMessage.model_validate(item) for item in json.loads(raw)]
    except Exception:
        logger.warning("redis.unavailable", extra={"operation": "get_cached_history"})
        return None


def set_cached_history(user_id: int, session_id: int, history: list[ChatHistoryMessage]) -> None:
    client = get_redis_client()
    if client is None:
        return
    try:
        payload = json.dumps([item.model_dump() for item in history])
        client.set(
            _history_key(user_id, session_id),
            payload,
            ex=settings.REDIS_CHAT_TTL_SECONDS,
        )
    except Exception:
        logger.warning("redis.unavailable", extra={"operation": "set_cached_history"})


def invalidate_cached_history(user_id: int, session_id: int) -> None:
    client = get_redis_client()
    if client is None:
        return
    try:
        client.delete(_history_key(user_id, session_id))
    except Exception:
        logger.warning("redis.unavailable", extra={"operation": "invalidate_cached_history"})
