from unittest.mock import Mock

import pytest
import requests

from app.config import settings
from app.services.embedding_providers import get_embedding_provider
from app.services.embedding_providers.base import EmbeddingModelUnavailableError
from app.services.embedding_providers.failover_embedding_provider import FailoverEmbeddingProvider
from app.services.embedding_providers.nvidia_embedding_provider import NvidiaEmbeddingProvider
from app.services.embedding_providers.ollama_embedding_provider import (
    OllamaEmbeddingProvider,
    _ollama_embed_url,
)


def _response(payload: dict, status_code: int = 200) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
    return response


def test_nvidia_embed_requires_api_key(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "")

    with pytest.raises(EmbeddingModelUnavailableError, match="NVIDIA_API_KEY"):
        NvidiaEmbeddingProvider().embed(["hello"])


def test_nvidia_embed_returns_vectors_in_input_order(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvapi-test")
    captured = {}

    def fake_post(session, url, *, headers, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _response(
            {
                "data": [
                    {"index": 1, "embedding": [0.4, 0.5]},
                    {"index": 0, "embedding": [0.1, 0.2]},
                ]
            }
        )

    monkeypatch.setattr(
        "app.services.embedding_providers.nvidia_embedding_provider.post_with_retry", fake_post
    )

    result = NvidiaEmbeddingProvider().embed(["first", "second"], input_type="passage")

    assert result == [[0.1, 0.2], [0.4, 0.5]]
    assert captured["url"] == f"{settings.NVIDIA_API_BASE_URL}/embeddings"
    assert captured["json"]["input"] == ["first", "second"]
    assert captured["json"]["input_type"] == "passage"
    assert captured["json"]["model"] == settings.EMBEDDING_MODEL


def test_nvidia_embed_raises_on_timeout(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvapi-test")

    def fake_post(*args, **kwargs):
        raise requests.Timeout("slow")

    monkeypatch.setattr(
        "app.services.embedding_providers.nvidia_embedding_provider.post_with_retry", fake_post
    )

    with pytest.raises(EmbeddingModelUnavailableError, match="timed out"):
        NvidiaEmbeddingProvider().embed(["hello"])


def test_nvidia_embed_raises_on_malformed_response(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setattr(
        "app.services.embedding_providers.nvidia_embedding_provider.post_with_retry",
        lambda *a, **k: _response({"unexpected": "shape"}),
    )

    with pytest.raises(EmbeddingModelUnavailableError, match="not in the expected shape"):
        NvidiaEmbeddingProvider().embed(["hello"])


def test_ollama_embed_url_swaps_chat_path_for_embed():
    original = settings.OLLAMA_URL
    try:
        settings.OLLAMA_URL = "http://localhost:11434/api/chat"
        assert _ollama_embed_url() == "http://localhost:11434/api/embed"
    finally:
        settings.OLLAMA_URL = original


def test_ollama_embed_returns_vectors(monkeypatch):
    captured = {}

    def fake_post(url, *, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _response({"embeddings": [[0.1, 0.2], [0.3, 0.4]]})

    monkeypatch.setattr(
        "app.services.embedding_providers.ollama_embedding_provider.requests.post", fake_post
    )

    result = OllamaEmbeddingProvider().embed(["a", "b"])

    assert result == [[0.1, 0.2], [0.3, 0.4]]
    assert captured["url"].endswith("/api/embed")
    assert captured["json"]["model"] == settings.OLLAMA_EMBEDDING_MODEL


def test_ollama_embed_raises_on_shape_mismatch(monkeypatch):
    monkeypatch.setattr(
        "app.services.embedding_providers.ollama_embedding_provider.requests.post",
        lambda *a, **k: _response({"embeddings": [[0.1, 0.2]]}),
    )

    with pytest.raises(EmbeddingModelUnavailableError, match="not in the expected shape"):
        OllamaEmbeddingProvider().embed(["a", "b"])


def test_ollama_embed_raises_on_connection_error(monkeypatch):
    def fake_post(*args, **kwargs):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(
        "app.services.embedding_providers.ollama_embedding_provider.requests.post", fake_post
    )

    with pytest.raises(EmbeddingModelUnavailableError, match="unavailable"):
        OllamaEmbeddingProvider().embed(["a"])


def test_failover_uses_nvidia_when_available():
    nvidia = Mock()
    nvidia.embed.return_value = [[0.1]]
    ollama = Mock()

    result = FailoverEmbeddingProvider(nvidia=nvidia, ollama=ollama).embed(["x"])

    assert result == [[0.1]]
    ollama.embed.assert_not_called()


def test_failover_falls_back_to_ollama_on_nvidia_failure():
    nvidia = Mock()
    nvidia.embed.side_effect = EmbeddingModelUnavailableError("down")
    ollama = Mock()
    ollama.embed.return_value = [[0.9]]

    result = FailoverEmbeddingProvider(nvidia=nvidia, ollama=ollama).embed(["x"])

    assert result == [[0.9]]
    ollama.embed.assert_called_once()


def test_get_embedding_provider_stays_local_for_ollama(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "ollama")

    assert isinstance(get_embedding_provider(), OllamaEmbeddingProvider)


def test_get_embedding_provider_defaults_to_failover(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "nvidia")

    assert isinstance(get_embedding_provider(), FailoverEmbeddingProvider)
