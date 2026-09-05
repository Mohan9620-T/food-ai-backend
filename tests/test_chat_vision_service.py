import base64

import pytest

from app.config import settings
from app.schemas.vision_result import VisionResult
from app.services.chat_vision_service import ChatVisionService
from app.services.image_parser_service import VisionModelUnavailableError


def vision_result(image_type="other", answer=None, items=None, uncertain_items=None):
    return VisionResult.model_validate(
        {
            "image_type": image_type,
            "answer": answer,
            "items": items or [],
            "uncertain_items": uncertain_items or [],
        }
    )


class StubVisionProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def infer(self, system_prompt, user_prompt, encoded_image):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "encoded_image": encoded_image,
            }
        )
        if self.error:
            raise self.error
        return self.result


def install_provider(monkeypatch, result=None, error=None):
    provider = StubVisionProvider(result=result, error=error)
    monkeypatch.setattr("app.services.chat_vision_service.get_vision_provider", lambda: provider)
    return provider


def test_describe_returns_natural_language_image_description(monkeypatch):
    provider = install_provider(
        monkeypatch,
        vision_result(
            items=[
                {
                    "name": "a bicycle",
                    "confidence": "high",
                    "visual_evidence": "two wheels beside a tree",
                }
            ]
        ),
    )
    result = ChatVisionService().describe(b"image-bytes", None)

    assert result == "I can see a bicycle (two wheels beside a tree)."
    assert provider.calls[0]["user_prompt"] == "Please describe this image."
    assert provider.calls[0]["encoded_image"] == base64.b64encode(b"image-bytes").decode("ascii")


def test_describe_passes_accompanying_user_question(monkeypatch):
    provider = install_provider(
        monkeypatch,
        vision_result(
            image_type="text",
            items=[
                {
                    "name": "OPEN",
                    "confidence": "high",
                    "visual_evidence": "clearly visible letters on the sign",
                }
            ],
        ),
    )
    result = ChatVisionService().describe(b"sign", "What does the sign say?")

    assert "OPEN" in result
    assert provider.calls[0]["user_prompt"] == "What does the sign say?"
    assert "Transcribe any clearly visible text" in provider.calls[0]["system_prompt"]


def test_describe_returns_direct_person_compliance_answer_instead_of_object_inventory(
    monkeypatch,
):
    direct_answer = (
        '[{"person":"center","missing":["hair net","gloves"],"uncertain":["face mask"]}]'
    )
    provider = install_provider(
        monkeypatch,
        vision_result(
            answer=direct_answer,
            items=[
                {
                    "name": "red lid",
                    "confidence": "high",
                    "visual_evidence": "held by the center person",
                }
            ],
        ),
    )
    result = ChatVisionService().describe(
        b"kitchen",
        "List each person and missing hair nets, face masks, and gloves as JSON.",
    )

    assert result == direct_answer
    system_prompt = " ".join(provider.calls[0]["system_prompt"].split())
    assert "inspect each visible person separately" in system_prompt
    assert "Do not replace a requested analysis with a generic" in system_prompt


def test_describe_raises_clear_error_when_vision_model_times_out(monkeypatch):
    install_provider(
        monkeypatch,
        error=VisionModelUnavailableError("Chat vision analysis timed out after 120 seconds."),
    )
    with pytest.raises(VisionModelUnavailableError, match="analysis timed out after"):
        ChatVisionService().describe(b"image", None)


def test_describe_raises_clear_error_when_ollama_is_unavailable(monkeypatch):
    install_provider(
        monkeypatch,
        error=VisionModelUnavailableError("Confirm Ollama is running and installed."),
    )
    with pytest.raises(VisionModelUnavailableError, match="Confirm Ollama is running"):
        ChatVisionService().describe(b"image", None)


def test_describe_returns_clear_fallback_for_empty_model_response(monkeypatch):
    install_provider(monkeypatch, error=ValueError("empty response"))
    assert ChatVisionService().describe(b"image", None) == (
        ChatVisionService.EMPTY_RESPONSE_MESSAGE
    )


def test_describe_uses_correct_article_for_other_image(monkeypatch):
    install_provider(monkeypatch, vision_result(image_type="other"))
    assert (
        ChatVisionService()
        .describe(b"image", None)
        .startswith("This appears to be an image of another type")
    )


def test_describe_returns_clear_fallback_for_malformed_structured_response(monkeypatch):
    install_provider(monkeypatch, error=ValueError("malformed JSON"))
    assert ChatVisionService().describe(b"image", None) == (
        ChatVisionService.EMPTY_RESPONSE_MESSAGE
    )


def test_describe_accepts_validated_structured_result_from_thinking_field(monkeypatch):
    install_provider(
        monkeypatch,
        vision_result(
            items=[
                {
                    "name": "idli",
                    "confidence": "high",
                    "visual_evidence": "round white steamed cakes",
                }
            ]
        ),
    )
    assert ChatVisionService().describe(b"image", None) == (
        "I can see idli (round white steamed cakes)."
    )


def test_describe_includes_model_uncertainty_in_user_response(monkeypatch):
    install_provider(
        monkeypatch,
        vision_result(
            image_type="food",
            items=[
                {
                    "name": "idli",
                    "confidence": "medium",
                    "visual_evidence": "round white steamed cakes",
                }
            ],
            uncertain_items=["the orange condiment may be sambar"],
        ),
    )
    result = ChatVisionService().describe(b"image", None)

    assert "likely idli" in result
    assert "I'm uncertain about: the orange condiment may be sambar." in result


def test_ocr_detected_text_is_included_in_the_single_vision_prompt(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_VISION_OCR_ENABLED", True)
    monkeypatch.setattr(
        ChatVisionService,
        "_extract_ocr_text",
        lambda self, image_bytes: "CAFE OPEN 7 AM",
    )
    provider = install_provider(monkeypatch, vision_result(image_type="text"))
    ChatVisionService().describe(b"image", "What does the sign say?")

    prompt = provider.calls[0]["user_prompt"]
    assert "The following text was detected in the image via OCR" in prompt
    assert "CAFE OPEN 7 AM" in prompt
    assert len(provider.calls) == 1


def test_no_meaningful_ocr_text_is_not_added_to_prompt(monkeypatch):
    monkeypatch.setattr(ChatVisionService, "_run_ocr", lambda image_bytes: " .  ")
    provider = install_provider(monkeypatch, vision_result())
    ChatVisionService().describe(b"image", None)

    assert "OCR TEXT" not in provider.calls[0]["user_prompt"]


def test_tesseract_not_installed_skips_ocr_and_still_calls_vision(monkeypatch):
    def missing_tesseract(image_bytes):
        raise OSError("tesseract executable was not found")

    monkeypatch.setattr(ChatVisionService, "_run_ocr", staticmethod(missing_tesseract))
    provider = install_provider(
        monkeypatch,
        vision_result(
            image_type="text",
            items=[
                {
                    "name": "document",
                    "confidence": "high",
                    "visual_evidence": "paper visible on a desk",
                }
            ],
        ),
    )
    result = ChatVisionService().describe(b"image", None)

    assert result == "I can see document (paper visible on a desk)."
    assert len(provider.calls) == 1
    assert "OCR TEXT" not in provider.calls[0]["user_prompt"]


def test_describe_returns_empty_response_when_provider_parse_fails(monkeypatch):
    install_provider(monkeypatch, error=ValueError("provider parse failed"))
    assert ChatVisionService().describe(b"image", None) == (
        ChatVisionService.EMPTY_RESPONSE_MESSAGE
    )
