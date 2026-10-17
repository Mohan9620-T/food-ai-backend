import asyncio
import hashlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import settings
from app.models.api_key import ApiKey
from app.models.user import User
from app.services import oauth_service
from app.services.llm_gateway import NvidiaGateway, retryable
from app.utils.security import create_access_token


@pytest.fixture
def accounts(client, db_session, monkeypatch):
    for name in ("ENABLE_API_KEYS", "ENABLE_ROLES", "ENABLE_OAUTH_LOGIN", "ENABLE_REQUEST_IDS"):
        monkeypatch.setattr(settings, name, True)
    db_session.add_all(
        [
            User(
                id=201,
                fullname="Member",
                email="member@example.com",
                password="unused",
                is_email_verified=True,
            ),
            User(
                id=202, fullname="Admin", email="admin@example.com", password="unused", role="admin"
            ),
        ]
    )
    db_session.commit()

    def headers(uid=201):
        return {
            "Authorization": "Bearer " + create_access_token({"sub": str(uid), "auth_version": 0})
        }

    return client, headers


def test_key_lifecycle_and_ownership(accounts, db_session):
    client, headers = accounts
    response = client.post("/users/keys", headers=headers(), json={"name": "CLI"})
    assert response.status_code == 201, response.text
    key = response.json()
    assert key["key"].startswith("fai-")
    row = db_session.get(ApiKey, key["id"])
    assert row.key_hash == hashlib.sha256(key["key"].encode()).hexdigest()
    listed = client.get("/users/keys", headers=headers()).json()
    assert "key" not in listed[0] and "key_hash" not in listed[0]
    assert client.get("/users/keys", headers={"X-API-Key": key["key"]}).status_code == 200
    db_session.refresh(row)
    assert row.last_used_at is not None
    assert client.get("/users/keys", headers=headers(202)).json() == []
    assert client.delete(f"/users/keys/{row.id}", headers=headers(202)).status_code == 404
    assert (
        client.post(
            "/users/keys", headers={"X-API-Key": key["key"]}, json={"name": "Child"}
        ).status_code
        == 403
    )
    assert client.delete(f"/users/keys/{row.id}", headers=headers()).status_code == 200
    assert client.get("/users/keys", headers={"X-API-Key": key["key"]}).status_code == 401
    assert client.get("/users/keys", headers={"X-API-Key": "bad"}).status_code == 401
    assert client.post("/users/keys", headers=headers(), json={"name": "  "}).status_code == 422


def test_flags_off(accounts, monkeypatch):
    client, headers = accounts
    for name in ("ENABLE_API_KEYS", "ENABLE_ROLES", "ENABLE_OAUTH_LOGIN", "ENABLE_REQUEST_IDS"):
        monkeypatch.setattr(settings, name, False)
    assert client.get("/users/keys", headers=headers()).status_code == 404
    assert client.get("/admin/models", headers=headers()).status_code == 404
    assert (
        client.post(
            "/users/oauth-login", json={"provider": "google", "id_token": "test"}
        ).status_code
        == 404
    )
    assert "x-request-id" not in client.get("/health/live").headers
    assert client.get("/users/auth-capabilities").json() == {
        "oauth": False,
        "firebase": None,
        "password_reset": True,
    }


@pytest.mark.parametrize("role,status", [("viewer", 403), ("member", 403), ("admin", 200)])
def test_role_is_read_from_database(accounts, db_session, role, status):
    client, headers = accounts
    user = db_session.get(User, 201)
    user.role = role
    db_session.commit()
    response = client.get("/admin/models", headers=headers())
    assert response.status_code == status
    assert (
        client.post("/nvidia-chat", headers=headers(), json={"text": "Hello"}).status_code == 403
        if role != "admin"
        else True
    )


def test_diagnostics_safe_degraded_health(accounts, monkeypatch):
    from app.api import admin

    client, headers = accounts
    monkeypatch.setattr(settings, "ENABLE_REDIS_CACHE", True)
    monkeypatch.setattr(settings, "ENABLE_SEMANTIC_RAG", True)
    monkeypatch.setattr(admin, "get_redis_client", lambda: None)
    monkeypatch.setattr(admin, "get_chroma_client", lambda: None)
    monkeypatch.setattr(
        admin.httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("secret omitted"))
    )
    response = client.get("/admin/health/detailed", headers=headers(202))
    assert response.status_code == 200
    checks = response.json()["checks"]
    assert checks["database"] == "up" and checks["redis"] == "down" and checks["storage"] == "up"
    assert "secret omitted" not in response.text


def test_oauth_verified_link_and_rejections(accounts, db_session, monkeypatch):
    client, _ = accounts
    claims = {"email": "MEMBER@example.com", "email_verified": True}
    monkeypatch.setattr(oauth_service.FirebaseVerifier, "verify", lambda *args: claims)
    request = {"provider": "google", "id_token": "test-provider-token"}
    response = client.post("/users/oauth-login", json=request)
    assert response.status_code == 200, response.text
    assert db_session.query(User).count() == 2
    assert response.json()["refresh_token"]
    claims["email_verified"] = False
    assert client.post("/users/oauth-login", json=request).status_code == 403
    claims["email_verified"] = True
    claims["email"] = "new@example.com"
    assert client.post("/users/oauth-login", json=request).status_code == 200
    assert db_session.query(User).filter_by(email="new@example.com").one().is_email_verified
    monkeypatch.setattr(
        oauth_service.FirebaseVerifier,
        "verify",
        lambda *args: (_ for _ in ()).throw(ValueError("private provider response")),
    )
    response = client.post("/users/oauth-login", json=request)
    assert response.status_code == 401 and "private" not in response.text
    assert (
        "test-provider-token"
        not in client.post(
            "/users/oauth-login", json={"provider": "bad", "id_token": "test-provider-token"}
        ).text
    )


@pytest.mark.parametrize(
    "override",
    [
        {"aud": "wrong"},
        {"iss": "wrong"},
        {"exp": 1},
        {"auth_time": 99999999999},
        {"sub": ""},
        {"firebase": {"sign_in_provider": "password"}},
    ],
)
def test_signed_identity_claims_rejected(monkeypatch, override):
    monkeypatch.setattr(settings, "OAUTH_FIREBASE_PROJECT_ID", "food-test")
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        oauth_service,
        "signing_keys",
        lambda: SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private.public_key())
        ),
    )
    claims = {
        "sub": "test-user",
        "iss": "https://securetoken.google.com/food-test",
        "aud": "food-test",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        "auth_time": int(time.time()),
        "firebase": {"sign_in_provider": "google.com"},
        **override,
    }
    with pytest.raises((ValueError, jwt.PyJWTError)):
        oauth_service.FirebaseVerifier().verify(
            jwt.encode(claims, private, algorithm="RS256"), "google"
        )


def test_request_ids_include_errors(accounts):
    client, _ = accounts
    response = client.get("/users/keys", headers={"X-Request-ID": "trace-test-123"})
    assert response.status_code == 401
    assert response.headers["x-request-id"] == response.json()["request_id"] == "trace-test-123"
    response = client.get("/health/live", headers={"X-Request-ID": "invalid id"})
    assert len(response.headers["x-request-id"]) == 32
    assert client.post("/users/oauth-login", json={}).json()["request_id"]


@pytest.mark.parametrize(
    "status,attempts",
    [
        (400, 1),
        (401, 1),
        (403, 1),
        (408, 3),
        (409, 3),
        (425, 3),
        (429, 3),
        (500, 3),
        (599, 3),
        (200, 1),
    ],
)
def test_gateway_retry_policy(monkeypatch, status, attempts):
    instance = NvidiaGateway()
    calls = []

    async def initialize():
        def handle(request):
            calls.append(request)
            return httpx.Response(status, json={"choices": [{"message": {"content": "Answer"}}]})

        instance._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        instance._semaphore = asyncio.Semaphore(2)

    monkeypatch.setattr(instance, "_initialize", initialize)
    monkeypatch.setattr(instance, "_delay", AsyncMock())
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 2)

    async def run():
        try:
            assert (await instance.post({}, 1)).status_code == status
        finally:
            await instance.close()

    asyncio.run(run())
    assert len(calls) == attempts
    assert retryable(status) == (attempts > 1)


def test_gateway_shared_pool_concurrency_stream_and_sync(monkeypatch):
    instance = NvidiaGateway()
    active = 0
    peak = 0
    clients = []

    async def initialize():
        async def handle(request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return httpx.Response(200, content=b'data: {"choices":[]}\n\ndata: [DONE]\n')

        instance._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        instance._semaphore = asyncio.Semaphore(2)
        clients.append(instance._client)

    monkeypatch.setattr(instance, "_initialize", initialize)

    async def run():
        try:
            await asyncio.gather(*(instance.post({}, 1) for _ in range(8)))
            await asyncio.to_thread(instance.post_sync, {}, 1)
            async with instance.stream({}, 1) as response:
                assert [line async for line in response.aiter_lines()] == [
                    'data: {"choices":[]}',
                    "data: [DONE]",
                ]
        finally:
            await instance.close()

    asyncio.run(run())
    assert peak == 2 and len(clients) == 1 and clients[0].is_closed


def test_gateway_transport_retry_and_no_stream_replay(monkeypatch):
    instance = NvidiaGateway()
    calls = []

    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: first\n\n"
            raise httpx.ReadError("network failed")

    async def initialize():
        def handle(request):
            calls.append(request)
            if len(calls) < 3:
                raise httpx.ConnectError("network failed")
            return httpx.Response(200, stream=BrokenStream())

        instance._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        instance._semaphore = asyncio.Semaphore(1)

    monkeypatch.setattr(instance, "_initialize", initialize)
    monkeypatch.setattr(instance, "_delay", AsyncMock())
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 2)

    async def run():
        try:
            with pytest.raises(httpx.ReadError):
                await instance.post({}, 1)
            assert len(calls) == 3
            lines = []
            with pytest.raises(httpx.ReadError):
                async with instance.stream({}, 1) as response:
                    async for line in response.aiter_lines():
                        lines.append(line)
            assert lines == ["data: first"] and len(calls) == 4
        finally:
            await instance.close()

    asyncio.run(run())


def test_gateway_service_integration_keeps_length_signal(monkeypatch):
    from app.services import chat_service, llm_gateway
    from app.services.chat_service import ChatService, _OutputLimitReached

    monkeypatch.setattr(settings, "ENABLE_LLM_GATEWAY", True)
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "fake-test-key")
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "https://test.invalid"),
        json={"choices": [{"message": {"content": "Answer"}, "finish_reason": "length"}]},
    )
    fake = SimpleNamespace(post=AsyncMock(return_value=response), post_sync=lambda *args: response)
    monkeypatch.setattr(llm_gateway, "gateway", fake)
    monkeypatch.setattr(chat_service, "gateway", fake)
    body = {"messages": [], "options": {"temperature": 0.5}}
    with pytest.raises(_OutputLimitReached):
        asyncio.run(ChatService()._complete_with_nvidia(body))
    with pytest.raises(_OutputLimitReached):
        ChatService()._chat_with_nvidia(body)


def test_valid_signed_identity_and_configuration(monkeypatch):
    monkeypatch.setattr(settings, "OAUTH_FIREBASE_PROJECT_ID", "")
    with pytest.raises(RuntimeError):
        oauth_service.FirebaseVerifier().verify("ignored", "google")
    monkeypatch.setattr(settings, "OAUTH_FIREBASE_PROJECT_ID", "food-test")
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        oauth_service,
        "signing_keys",
        lambda: SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private.public_key())
        ),
    )
    claims = {
        "sub": "test-user",
        "iss": "https://securetoken.google.com/food-test",
        "aud": "food-test",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        "auth_time": int(time.time()),
        "firebase": {"sign_in_provider": "microsoft.com"},
    }
    assert (
        oauth_service.FirebaseVerifier().verify(
            jwt.encode(claims, private, algorithm="RS256"), "microsoft"
        )["sub"]
        == "test-user"
    )


def test_tracing_uses_existing_sdk_without_network(monkeypatch):
    pytest.importorskip("opentelemetry.instrumentation.fastapi")
    pytest.importorskip("opentelemetry.instrumentation.httpx")
    pytest.importorskip("opentelemetry.instrumentation.sqlalchemy")
    from fastapi import FastAPI
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc import trace_exporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from sqlalchemy import create_engine

    from app.services.observability import setup_tracing

    monkeypatch.setattr(settings, "OTEL_ENABLED", True)
    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", lambda **kwargs: InMemorySpanExporter())
    monkeypatch.setattr(trace, "set_tracer_provider", lambda provider: None)
    instrument = []
    monkeypatch.setattr(
        FastAPIInstrumentor, "instrument_app", lambda *args, **kwargs: instrument.append("fastapi")
    )
    monkeypatch.setattr(
        HTTPXClientInstrumentor, "instrument", lambda *args, **kwargs: instrument.append("httpx")
    )
    monkeypatch.setattr(
        SQLAlchemyInstrumentor,
        "instrument",
        lambda *args, **kwargs: instrument.append("sqlalchemy"),
    )
    setup_tracing(FastAPI(), create_engine("sqlite://"))
    assert instrument == ["fastapi", "httpx", "sqlalchemy"]


def test_api_key_observes_live_role_changes(accounts, db_session):
    client, headers = accounts
    key = client.post("/users/keys", headers=headers(), json={"name": "Admin tools"})
    assert key.headers["cache-control"] == "no-store"
    auth = {"X-API-Key": key.json()["key"]}
    assert client.get("/admin/models", headers=auth).status_code == 403
    user = db_session.get(User, 201)
    user.role = "admin"
    db_session.commit()
    assert client.get("/admin/models", headers=auth).status_code == 200


def test_missing_tracing_instrumentation_degrades_safely(monkeypatch):
    import builtins

    from app.services.observability import setup_tracing

    original_import = builtins.__import__

    def without_instrumentation(name, *args, **kwargs):
        if name.startswith("opentelemetry.instrumentation"):
            raise ImportError("optional instrumentation missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(settings, "OTEL_ENABLED", True)
    monkeypatch.setattr(builtins, "__import__", without_instrumentation)
    setup_tracing(None, None)
