import fakeredis
import pytest
import redis

from app.config import settings
from app.database import redis_client
from app.schemas.chat import ChatHistoryMessage


def test_history_key_namespaces_by_user_and_session():
    assert redis_client._history_key(1, 5) == "foodai:chat:1:5:recent"
    assert redis_client._history_key(2, 5) != redis_client._history_key(1, 5)
    assert redis_client._history_key(1, 6) != redis_client._history_key(1, 5)


def test_get_redis_client_returns_none_when_cache_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_REDIS_CACHE", False)

    assert redis_client.get_redis_client() is None


def test_cache_disabled_helpers_are_no_ops(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_REDIS_CACHE", False)
    history = [ChatHistoryMessage(role="user", content="hi")]

    redis_client.set_cached_history(1, 1, history)
    assert redis_client.get_cached_history(1, 1) is None
    redis_client.invalidate_cached_history(1, 1)


@pytest.fixture
def fake_client(monkeypatch):
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(settings, "ENABLE_REDIS_CACHE", True)
    monkeypatch.setattr(redis_client, "get_redis_client", lambda: client)
    return client


def test_cache_set_then_get_roundtrip(fake_client):
    history = [
        ChatHistoryMessage(role="user", content="What should I eat?"),
        ChatHistoryMessage(role="assistant", content="Try a balanced thali."),
    ]

    redis_client.set_cached_history(1, 5, history)
    cached = redis_client.get_cached_history(1, 5)

    assert cached == history


def test_cache_miss_returns_none(fake_client):
    assert redis_client.get_cached_history(1, 999) is None


def test_invalidate_cached_history_clears_the_key(fake_client):
    history = [ChatHistoryMessage(role="user", content="hi")]
    redis_client.set_cached_history(1, 5, history)

    redis_client.invalidate_cached_history(1, 5)

    assert redis_client.get_cached_history(1, 5) is None


def test_cached_history_respects_configured_ttl(fake_client, monkeypatch):
    monkeypatch.setattr(settings, "REDIS_CHAT_TTL_SECONDS", 60)
    history = [ChatHistoryMessage(role="user", content="hi")]

    redis_client.set_cached_history(1, 5, history)

    assert fake_client.ttl(redis_client._history_key(1, 5)) == 60


def test_history_cache_is_isolated_per_user(fake_client):
    redis_client.set_cached_history(1, 5, [ChatHistoryMessage(role="user", content="user one")])
    redis_client.set_cached_history(2, 5, [ChatHistoryMessage(role="user", content="user two")])

    assert redis_client.get_cached_history(1, 5)[0].content == "user one"
    assert redis_client.get_cached_history(2, 5)[0].content == "user two"


class _RaisingClient:
    def get(self, *args, **kwargs):
        raise redis.RedisError("connection refused")

    def set(self, *args, **kwargs):
        raise redis.RedisError("connection refused")

    def delete(self, *args, **kwargs):
        raise redis.RedisError("connection refused")


def test_get_cached_history_degrades_gracefully_on_redis_error(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_REDIS_CACHE", True)
    monkeypatch.setattr(redis_client, "get_redis_client", lambda: _RaisingClient())

    assert redis_client.get_cached_history(1, 5) is None


def test_set_cached_history_degrades_gracefully_on_redis_error(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_REDIS_CACHE", True)
    monkeypatch.setattr(redis_client, "get_redis_client", lambda: _RaisingClient())

    redis_client.set_cached_history(1, 5, [ChatHistoryMessage(role="user", content="hi")])


def test_invalidate_cached_history_degrades_gracefully_on_redis_error(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_REDIS_CACHE", True)
    monkeypatch.setattr(redis_client, "get_redis_client", lambda: _RaisingClient())

    redis_client.invalidate_cached_history(1, 5)


def _register_and_login(client, email):
    client.post("/users/", json={"fullname": "Chat User", "email": email, "password": "chat12345"})
    login_response = client.post("/users/login", json={"email": email, "password": "chat12345"})
    return login_response.json()["access_token"]


def test_chat_reads_history_from_redis_cache_instead_of_postgres(client, monkeypatch, fake_client):
    from jose import jwt

    from app.services.chat_service import ChatService
    from app.utils.security import ALGORITHM, SECRET_KEY

    token = _register_and_login(client, "redis-cache@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    user_id = int(jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])["sub"])

    session_id = client.post(
        "/chat/sessions", json={"title": "Cached session"}, headers=headers
    ).json()["id"]
    # Pre-populate the cache with content that does not exist in Postgres, so a
    # correct cache-aside read can only come from here, never from a DB fallback.
    redis_client.set_cached_history(
        user_id, session_id, [ChatHistoryMessage(role="user", content="from-redis-cache-only")]
    )

    captured_histories = []

    def fake_chat(self, message, history, reference_history, **kwargs):
        captured_histories.append([item.content for item in history])
        return "Sure, boss."

    monkeypatch.setattr(ChatService, "chat", fake_chat)
    response = client.post(
        f"/chat/?session_id={session_id}",
        json={"message": "Hello again", "history": [], "reference_history": []},
        headers=headers,
    )

    assert response.status_code == 200
    assert captured_histories[0] == ["from-redis-cache-only"]


def test_chat_invalidates_cache_after_a_new_turn_so_history_stays_fresh(
    client, monkeypatch, fake_client
):
    from app.services.chat_service import ChatService

    token = _register_and_login(client, "redis-invalidate@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    seen_histories = []

    def fake_chat(self, message, history, reference_history, **kwargs):
        seen_histories.append([item.content for item in history])
        return f"Answer {len(seen_histories)}"

    monkeypatch.setattr(ChatService, "chat", fake_chat)
    first = client.post(
        "/chat/",
        json={"message": "First message", "history": [], "reference_history": []},
        headers=headers,
    )
    session_id = first.json()["session_id"]

    second = client.post(
        f"/chat/?session_id={session_id}",
        json={"message": "Second message", "history": [], "reference_history": []},
        headers=headers,
    )

    assert second.status_code == 200
    assert seen_histories[1] == ["First message", "Answer 1"]
