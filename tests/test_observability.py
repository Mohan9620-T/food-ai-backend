from unittest.mock import MagicMock

import httpx

from app import main


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


def test_metrics_exposes_prometheus_request_metrics(client):
    client.get("/")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "http_requests_total" in response.text
