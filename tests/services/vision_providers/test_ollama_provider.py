import json
from unittest.mock import Mock

import pytest
import requests

from app.services.image_parser_service import VisionModelUnavailableError
from app.services.vision_providers.ollama_provider import OllamaVisionProvider


def valid_result_json() -> str:
    return json.dumps(
        {
            "image_type": "food",
            "answer": "The image contains idli.",
            "items": [
                {
                    "name": "idli",
                    "confidence": "high",
                    "visual_evidence": "round white steamed cakes",
                }
            ],
            "uncertain_items": [],
        }
    )


def response_with(content: str) -> Mock:
    response = Mock()
    response.json.return_value = {"message": {"content": content}}
    return response


def test_infer_returns_validated_result(monkeypatch):
    post = Mock(return_value=response_with(valid_result_json()))
    monkeypatch.setattr("requests.post", post)

    result = OllamaVisionProvider().infer("system", "user", "encoded-image")

    assert result.image_type == "food"
    assert result.items[0].name == "idli"
    payload = post.call_args.kwargs["json"]
    assert payload["messages"][-1] == {
        "role": "user",
        "content": "user",
        "images": ["encoded-image"],
    }


def test_infer_accepts_valid_result_from_thinking_field(monkeypatch):
    response = Mock()
    response.json.return_value = {"message": {"content": "", "thinking": valid_result_json()}}
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: response)

    assert OllamaVisionProvider().infer("system", "user", "image").image_type == "food"


def test_infer_rejects_invalid_json(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: response_with("not-json"))

    with pytest.raises(ValueError, match="Ollama vision response was not valid JSON"):
        OllamaVisionProvider().infer("system", "user", "encoded-image")


def test_infer_translates_timeout(monkeypatch):
    def timeout(*args, **kwargs):
        raise requests.Timeout("timed out")

    monkeypatch.setattr("requests.post", timeout)

    with pytest.raises(VisionModelUnavailableError, match="analysis timed out after"):
        OllamaVisionProvider().infer("system", "user", "encoded-image")


def test_infer_translates_connection_failure(monkeypatch):
    def unavailable(*args, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr("requests.post", unavailable)

    with pytest.raises(VisionModelUnavailableError, match="Confirm Ollama is running"):
        OllamaVisionProvider().infer("system", "user", "encoded-image")
