import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.config import settings


def preflight(client, origin):
    return client.options(
        "/users/login",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization",
        },
    )


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:55035",
        "http://localhost:4200",
        "http://127.0.0.1:55035",
        "http://[::1]:55035",
        "https://localhost:55035",
    ],
)
def test_development_login_preflight_allows_loopback_ports(client, origin):
    response = preflight(client, origin)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "Origin" in response.headers["vary"]
    assert "POST" in response.headers["access-control-allow-methods"]


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost.evil.example:55035",
        "http://127.0.0.1.evil.example:55035",
        "http://evil-localhost:55035",
        "http://192.168.1.20:55035",
        "null",
        "file://localhost",
    ],
)
def test_development_rejects_non_loopback_origins(client, origin):
    response = preflight(client, origin)
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("environment", ["production", "staging", "test", ""])
def test_other_environments_only_allow_explicit_origins(monkeypatch, environment):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", environment)
    deployed_app = FastAPI()
    deployed_app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://food.example.com"],
        allow_origin_regex=settings.local_development_origin_regex(),
        allow_credentials=True,
        allow_methods=["POST"],
        allow_headers=["content-type", "authorization"],
    )
    with TestClient(deployed_app) as client:
        allowed = preflight(client, "https://food.example.com")
        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-origin"] == "https://food.example.com"
        for origin in ("http://localhost:55035", "http://127.0.0.1:4200", "http://[::1]:55035"):
            denied = preflight(client, origin)
            assert denied.status_code == 400
            assert "access-control-allow-origin" not in denied.headers
