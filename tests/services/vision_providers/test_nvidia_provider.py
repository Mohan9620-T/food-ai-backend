import json
from unittest.mock import Mock

import pytest
import requests

from app.config import settings
from app.services.image_parser_service import VisionModelUnavailableError
from app.services.vision_providers.nvidia_provider import (
    NvidiaConfigurationError,
    NvidiaVisionProvider,
)


def valid_result_json() -> str:
    return json.dumps(
        {
            "image_type": "food",
            "answer": "The image contains idli.",
            "items": [],
            "uncertain_items": [],
        }
    )


def response_with(content: str) -> Mock:
    response = Mock()
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


def test_infer_requires_api_key(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "")

    with pytest.raises(NvidiaConfigurationError, match="NVIDIA_API_KEY is not configured"):
        NvidiaVisionProvider().infer("system", "user", "encoded-image")


def test_infer_returns_validated_result(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: response_with(valid_result_json()))

    result = NvidiaVisionProvider().infer("system", "user", "encoded-image")

    assert result.answer == "The image contains idli."


def test_infer_strips_json_markdown_fence(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvapi-test")
    fenced = f"```json\n{valid_result_json()}\n```"
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: response_with(fenced))

    assert NvidiaVisionProvider().infer("system", "user", "encoded-image").image_type == "food"


def test_infer_rejects_malformed_json(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: response_with("not-json"))

    with pytest.raises(ValueError, match="NVIDIA vision response was not valid JSON"):
        NvidiaVisionProvider().infer("system", "user", "encoded-image")


def test_infer_rejects_invalid_schema(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setattr(
        "requests.post",
        lambda *args, **kwargs: response_with(json.dumps({"image_type": "unknown"})),
    )

    with pytest.raises(ValueError, match="NVIDIA vision response was not valid JSON"):
        NvidiaVisionProvider().infer("system", "user", "encoded-image")


def test_infer_translates_timeout(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvapi-test")

    def timeout(*args, **kwargs):
        raise requests.Timeout("timed out")

    monkeypatch.setattr("requests.post", timeout)

    with pytest.raises(VisionModelUnavailableError, match="NVIDIA vision analysis timed out"):
        NvidiaVisionProvider().infer("system", "user", "encoded-image")


def test_infer_translates_http_error(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvapi-test")
    response = response_with(valid_result_json())
    response.raise_for_status.side_effect = requests.HTTPError("rate limited")
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: response)

    with pytest.raises(VisionModelUnavailableError, match="NVIDIA vision API unavailable"):
        NvidiaVisionProvider().infer("system", "user", "encoded-image")


def test_infer_uses_openai_image_url_payload(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvapi-test")
    post = Mock(return_value=response_with(valid_result_json()))
    monkeypatch.setattr("requests.post", post)

    NvidiaVisionProvider().infer("system", "user question", "encoded-image")

    assert post.call_args.args[0] == f"{settings.NVIDIA_API_BASE_URL}/chat/completions"
    assert post.call_args.kwargs["headers"] == {
        "Authorization": "Bearer nvapi-test",
        "Content-Type": "application/json",
    }
    assert post.call_args.kwargs["timeout"] == settings.NVIDIA_VISION_TIMEOUT_SECONDS
    payload = post.call_args.kwargs["json"]
    assert payload["model"] == settings.NVIDIA_CHAT_VISION_MODEL
    assert payload["stream"] is False
    assert payload["temperature"] == 0
    assert payload["max_tokens"] == 1024
    assert "Respond with ONLY a single JSON object" in payload["messages"][0]["content"]
    user_content = payload["messages"][1]["content"]
    assert user_content == [
        {"type": "text", "text": "user question"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,encoded-image"},
        },
    ]
    assert "images" not in payload["messages"][1]
