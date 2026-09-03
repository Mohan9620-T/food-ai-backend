import base64
import json

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


def vision_content(
    image_type="other",
    answer=None,
    items=None,
    uncertain_items=None,
):
    return json.dumps({
        "image_type": image_type,
        "answer": answer,
        "items": items or [],
        "uncertain_items": uncertain_items or [],
    })


def test_describe_returns_natural_language_image_description(monkeypatch):
    captured = {}

    def post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return FakeResponse({"message": {"content": vision_content(items=[{
            "name": "a bicycle",
            "confidence": "high",
            "visual_evidence": "two wheels beside a tree",
        }])}})

    monkeypatch.setattr("requests.post", post)
    result = ChatVisionService().describe(b"image-bytes", None)

    assert result == "I can see a bicycle (two wheels beside a tree)."
    assert captured["url"] == settings.OLLAMA_URL
    assert captured["json"]["model"] == settings.OLLAMA_CHAT_VISION_MODEL
    assert captured["json"]["keep_alive"] == settings.OLLAMA_KEEP_ALIVE
    assert captured["json"]["think"] is False
    assert captured["timeout"] == settings.OLLAMA_CHAT_VISION_TIMEOUT_SECONDS
    user = captured["json"]["messages"][-1]
    assert user["content"] == "Please describe this image."
    assert user["images"] == [base64.b64encode(b"image-bytes").decode("ascii")]
    assert captured["json"]["format"]["type"] == "object"
    assert captured["json"]["options"] == {
        "temperature": 0,
        "num_ctx": 8192,
        "num_predict": 1024,
    }


def test_describe_passes_accompanying_user_question(monkeypatch):
    captured = {}

    def post(url, **kwargs):
        captured["body"] = kwargs["json"]
        return FakeResponse({"message": {"content": vision_content(
            image_type="text",
            items=[{
                "name": "OPEN",
                "confidence": "high",
                "visual_evidence": "clearly visible letters on the sign",
            }],
        )}})

    monkeypatch.setattr("requests.post", post)
    result = ChatVisionService().describe(b"sign", "What does the sign say?")

    assert "OPEN" in result
    assert captured["body"]["messages"][-1]["content"] == "What does the sign say?"
    assert "Transcribe any clearly visible text" in captured["body"]["messages"][0]["content"]


def test_describe_returns_direct_person_compliance_answer_instead_of_object_inventory(monkeypatch):
    captured = {}
    direct_answer = (
        '[{"person":"center","missing":["hair net","gloves"],'
        '"uncertain":["face mask"]}]'
    )

    def post(url, **kwargs):
        captured["body"] = kwargs["json"]
        return FakeResponse({"message": {"content": vision_content(
            answer=direct_answer,
            items=[{
                "name": "red lid",
                "confidence": "high",
                "visual_evidence": "held by the center person",
            }],
        )}})

    monkeypatch.setattr("requests.post", post)
    result = ChatVisionService().describe(
        b"kitchen",
        "List each person and missing hair nets, face masks, and gloves as JSON.",
    )

    assert result == direct_answer
    system_prompt = " ".join(
        captured["body"]["messages"][0]["content"].split()
    )
    assert "inspect each visible person separately" in system_prompt
    assert "Do not replace a requested analysis with a generic" in system_prompt


def test_describe_raises_clear_error_when_vision_model_times_out(monkeypatch):
    def unavailable(url, **kwargs):
        raise requests.Timeout("CPU inference timed out")

    monkeypatch.setattr("requests.post", unavailable)
    with pytest.raises(VisionModelUnavailableError, match="analysis timed out after"):
        ChatVisionService().describe(b"image", None)


def test_describe_raises_clear_error_when_ollama_is_unavailable(monkeypatch):
    def unavailable(url, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr("requests.post", unavailable)
    with pytest.raises(VisionModelUnavailableError, match="Confirm Ollama is running"):
        ChatVisionService().describe(b"image", None)


def test_describe_returns_clear_fallback_for_empty_model_response(monkeypatch):
    monkeypatch.setattr(
        "requests.post",
        lambda url, **kwargs: FakeResponse({"message": {"content": "  "}}),
    )
    assert ChatVisionService().describe(b"image", None) == (
        ChatVisionService.EMPTY_RESPONSE_MESSAGE
    )


def test_describe_uses_correct_article_for_other_image(monkeypatch):
    monkeypatch.setattr(
        "requests.post",
        lambda url, **kwargs: FakeResponse({
            "message": {"content": vision_content(image_type="other")}
        }),
    )

    assert ChatVisionService().describe(b"image", None).startswith(
        "This appears to be an image of another type"
    )


def test_describe_returns_clear_fallback_for_malformed_structured_response(monkeypatch):
    monkeypatch.setattr(
        "requests.post",
        lambda url, **kwargs: FakeResponse({"message": {"content": "not-json"}}),
    )
    assert ChatVisionService().describe(b"image", None) == (
        ChatVisionService.EMPTY_RESPONSE_MESSAGE
    )


def test_describe_accepts_validated_structured_result_from_thinking_field(monkeypatch):
    monkeypatch.setattr(
        "requests.post",
        lambda url, **kwargs: FakeResponse({"message": {
            "content": "",
            "thinking": vision_content(items=[{
                "name": "idli",
                "confidence": "high",
                "visual_evidence": "round white steamed cakes",
            }]),
        }}),
    )

    assert ChatVisionService().describe(b"image", None) == (
        "I can see idli (round white steamed cakes)."
    )

def test_describe_includes_model_uncertainty_in_user_response(monkeypatch):
    monkeypatch.setattr(
        "requests.post",
        lambda url, **kwargs: FakeResponse({"message": {"content": vision_content(
            image_type="food",
            items=[{
                "name": "idli",
                "confidence": "medium",
                "visual_evidence": "round white steamed cakes",
            }],
            uncertain_items=["the orange condiment may be sambar"],
        )}}),
    )

    result = ChatVisionService().describe(b"image", None)

    assert "likely idli" in result
    assert "I'm uncertain about: the orange condiment may be sambar." in result


def test_ocr_detected_text_is_included_in_the_single_vision_prompt(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        ChatVisionService,
        "_extract_ocr_text",
        lambda self, image_bytes: "CAFE OPEN 7 AM",
    )

    def post(url, **kwargs):
        captured["body"] = kwargs["json"]
        return FakeResponse({"message": {"content": vision_content(
            image_type="text",
            items=[{
                "name": "OPEN 7 AM",
                "confidence": "high",
                "visual_evidence": "OCR-supported sign text",
            }],
        )}})

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
        return FakeResponse({"message": {"content": vision_content()}})

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
        return FakeResponse({"message": {"content": vision_content(
            image_type="text",
            items=[{
                "name": "document",
                "confidence": "high",
                "visual_evidence": "paper visible on a desk",
            }],
        )}})

    monkeypatch.setattr(ChatVisionService, "_run_ocr", staticmethod(missing_tesseract))
    monkeypatch.setattr("requests.post", post)
    result = ChatVisionService().describe(b"image", None)

    assert result == "I can see document (paper visible on a desk)."
    assert captured["calls"] == 1
    assert "OCR TEXT" not in captured["body"]["messages"][-1]["content"]
