from unittest.mock import MagicMock

import httpx

from app import main
from app.config import settings


def test_health_remains_backward_compatible(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "Healthy"}


def test_liveness_does_not_check_dependencies(client, monkeypatch):
    connect = MagicMock(side_effect=AssertionError("database should not be checked"))
    monkeypatch.setattr(main.engine, "connect", connect)

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "Healthy"}
    connect.assert_not_called()


def test_readiness_reports_healthy_dependencies(client, monkeypatch):
    connection = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = connection
    monkeypatch.setattr(main.engine, "connect", MagicMock(return_value=context))

    ollama_response = MagicMock()
    ollama_response.raise_for_status.return_value = None
    ollama_get = MagicMock(return_value=ollama_response)
    monkeypatch.setattr(main.httpx, "get", ollama_get)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "Ready",
        "checks": {"database": "up", "ollama": "up"},
    }
    connection.execute.assert_called_once()
    ollama_get.assert_called_once_with(main._ollama_health_url(), timeout=2.0)


def test_readiness_returns_503_when_dependencies_are_unavailable(client, monkeypatch):
    monkeypatch.setattr(main.engine, "connect", MagicMock(side_effect=OSError("db offline")))
    monkeypatch.setattr(
        main.httpx,
        "get",
        MagicMock(side_effect=httpx.ConnectError("ollama offline")),
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "Not Ready",
        "checks": {"database": "down", "ollama": "down"},
    }


def test_readiness_ignores_ollama_when_not_required(client, monkeypatch):
    connection = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = connection
    monkeypatch.setattr(main.engine, "connect", MagicMock(return_value=context))
    monkeypatch.setattr(
        main.httpx, "get", MagicMock(side_effect=httpx.ConnectError("ollama offline"))
    )
    monkeypatch.setattr(settings, "OLLAMA_REQUIRED_FOR_READINESS", False)

    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "Ready"
    assert body["checks"]["ollama"] == "down"


def test_readiness_still_gates_on_ollama_by_default(client, monkeypatch):
    connection = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = connection
    monkeypatch.setattr(main.engine, "connect", MagicMock(return_value=context))
    monkeypatch.setattr(
        main.httpx, "get", MagicMock(side_effect=httpx.ConnectError("ollama offline"))
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["ollama"] == "down"


def test_readiness_ignores_redis_when_cache_disabled(client, monkeypatch):
    connection = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = connection
    monkeypatch.setattr(main.engine, "connect", MagicMock(return_value=context))
    ollama_response = MagicMock()
    ollama_response.raise_for_status.return_value = None
    monkeypatch.setattr(main.httpx, "get", MagicMock(return_value=ollama_response))
    monkeypatch.setattr(settings, "ENABLE_REDIS_CACHE", False)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert "redis" not in response.json()["checks"]


def test_readiness_reports_redis_down_without_affecting_overall_readiness(client, monkeypatch):
    connection = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = connection
    monkeypatch.setattr(main.engine, "connect", MagicMock(return_value=context))
    ollama_response = MagicMock()
    ollama_response.raise_for_status.return_value = None
    monkeypatch.setattr(main.httpx, "get", MagicMock(return_value=ollama_response))
    monkeypatch.setattr(settings, "ENABLE_REDIS_CACHE", True)
    monkeypatch.setattr(main, "get_redis_client", MagicMock(return_value=None))

    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "Ready"
    assert body["checks"]["redis"] == "down"


def test_readiness_reports_redis_up_when_reachable(client, monkeypatch):
    connection = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = connection
    monkeypatch.setattr(main.engine, "connect", MagicMock(return_value=context))
    ollama_response = MagicMock()
    ollama_response.raise_for_status.return_value = None
    monkeypatch.setattr(main.httpx, "get", MagicMock(return_value=ollama_response))
    monkeypatch.setattr(settings, "ENABLE_REDIS_CACHE", True)
    fake_client = MagicMock()
    fake_client.ping.return_value = True
    monkeypatch.setattr(main, "get_redis_client", MagicMock(return_value=fake_client))

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["redis"] == "up"


def test_metrics_exposes_prometheus_request_metrics(client):
    client.get("/")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "http_requests_total" in response.text
