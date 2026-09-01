import base64

import pytest
import requests

from app.config import settings
from app.services.chat_vision_service import ChatVisionService
from app.services.image_parser_service import VisionModelUnavailableError


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_describe_returns_natural_language_image_description(monkeypatch):
    captured = {}

    def post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return FakeResponse({"message": {"content": "A bicycle is parked beside a tree."}})

    monkeypatch.setattr("requests.post", post)
    result = ChatVisionService().describe(b"image-bytes", None)

    assert result == "A bicycle is parked beside a tree."
    assert captured["url"] == settings.OLLAMA_URL
    assert captured["json"]["model"] == settings.OLLAMA_CHAT_VISION_MODEL
    assert captured["json"]["keep_alive"] == settings.OLLAMA_KEEP_ALIVE
    assert captured["timeout"] == settings.OLLAMA_CHAT_VISION_TIMEOUT_SECONDS
    user = captured["json"]["messages"][-1]
    assert user["content"] == "Please describe this image."
    assert user["images"] == [base64.b64encode(b"image-bytes").decode("ascii")]
    assert "format" not in captured["json"]


def test_describe_passes_accompanying_user_question(monkeypatch):
    captured = {}

    def post(url, **kwargs):
        captured["body"] = kwargs["json"]
        return FakeResponse({"message": {"content": "The sign says OPEN."}})

    monkeypatch.setattr("requests.post", post)
    result = ChatVisionService().describe(b"sign", "What does the sign say?")

    assert result == "The sign says OPEN."
    assert captured["body"]["messages"][-1]["content"] == "What does the sign say?"
    assert "Transcribe any clearly visible text" in captured["body"]["messages"][0]["content"]


def test_describe_raises_clear_error_when_vision_model_is_unavailable(monkeypatch):
    def unavailable(url, **kwargs):
        raise requests.Timeout("CPU inference timed out")

    monkeypatch.setattr("requests.post", unavailable)
    with pytest.raises(VisionModelUnavailableError, match="Chat vision model unavailable"):
        ChatVisionService().describe(b"image", None)


def test_describe_returns_clear_fallback_for_empty_model_response(monkeypatch):
    monkeypatch.setattr(
        "requests.post",
        lambda url, **kwargs: FakeResponse({"message": {"content": "  "}}),
    )
    assert ChatVisionService().describe(b"image", None) == (
        ChatVisionService.EMPTY_RESPONSE_MESSAGE
    )


def test_ocr_detected_text_is_included_in_the_single_vision_prompt(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        ChatVisionService,
        "_extract_ocr_text",
        lambda self, image_bytes: "CAFE OPEN 7 AM",
    )

    def post(url, **kwargs):
        captured["body"] = kwargs["json"]
        return FakeResponse({"message": {"content": "The cafe sign says OPEN 7 AM."}})

    monkeypatch.setattr("requests.post", post)
    ChatVisionService().describe(b"image", "What does the sign say?")

    prompt = captured["body"]["messages"][-1]["content"]
    assert "The following text was detected in the image via OCR" in prompt
    assert "CAFE OPEN 7 AM" in prompt
    assert len(captured["body"]["messages"]) == 2


def test_no_meaningful_ocr_text_is_not_added_to_prompt(monkeypatch):
    captured = {}
    monkeypatch.setattr(ChatVisionService, "_run_ocr", lambda image_bytes: " .  ")

    def post(url, **kwargs):
        captured["body"] = kwargs["json"]
        return FakeResponse({"message": {"content": "A quiet street."}})

    monkeypatch.setattr("requests.post", post)
    ChatVisionService().describe(b"image", None)

    assert "OCR TEXT" not in captured["body"]["messages"][-1]["content"]


def test_tesseract_not_installed_skips_ocr_and_still_calls_vision(monkeypatch):
    captured = {"calls": 0}

    def missing_tesseract(image_bytes):
        raise OSError("tesseract executable was not found")

    def post(url, **kwargs):
        captured["calls"] += 1
        captured["body"] = kwargs["json"]
        return FakeResponse({"message": {"content": "A document on a desk."}})

    monkeypatch.setattr(ChatVisionService, "_run_ocr", staticmethod(missing_tesseract))
    monkeypatch.setattr("requests.post", post)
    result = ChatVisionService().describe(b"image", None)

    assert result == "A document on a desk."
    assert captured["calls"] == 1
    assert "OCR TEXT" not in captured["body"]["messages"][-1]["content"]
