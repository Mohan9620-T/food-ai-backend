import base64

import pytest

from app.config import settings
from app.schemas.chat import ChatHistoryMessage
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

    def infer(self, system_prompt, user_prompt, encoded_image, **kwargs):
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


def test_generated_comparison_combines_visual_evidence_and_saved_intent_without_web(monkeypatch):
    from app.services.chat_vision_service import _ImageComparisonChatService

    evidence = "Image 1: glasses, pink shirt. Image 2: dark jacket, white shirt."
    install_provider(monkeypatch, vision_result(answer=evidence))
    history = [
        ChatHistoryMessage(
            role="assistant",
            content="Saved image-generation provenance: intended subject Example Minister, unverified likeness.",
        )
    ]
    calls = []

    async def complete(self, message, conversation, references):
        assert self._maybe_web_search(message, force=True, history=conversation) is None
        calls.append((message, conversation, references))
        yield "## Intended subject\nThe request named Example Minister.\n"
        yield "The two images depict different individuals. "
        yield "## Visible differences\nImage 1 has glasses. Image 2 has a dark jacket."

    monkeypatch.setattr(_ImageComparisonChatService, "stream_chat", complete)
    answer = ChatVisionService().describe(
        b"generated", "Compare both images", history, additional_images=[b"reference"]
    )
    assert "Example Minister" in answer and "Visible differences" in answer
    assert "different individuals" not in answer
    assert calls[0][1] == history
    assert evidence in calls[0][2][0].content


def test_uploaded_face_identity_question_is_answered_without_face_recognition(monkeypatch):
    provider = install_provider(monkeypatch, vision_result(answer="Should not run"))
    answer = ChatVisionService().describe(b"portrait", "who is he?")
    assert "can't identify a person from their face" in answer
    assert not provider.calls


def test_comparison_drops_unsupported_identity_claim_but_keeps_visible_features(monkeypatch):
    install_provider(
        monkeypatch,
        vision_result(
            answer="Image 1 wears glasses. These are different people. Image 2 wears a dark jacket."
        ),
    )
    answer = ChatVisionService().describe(
        b"first", "Compare these images", additional_images=[b"second"]
    )
    assert "different people" not in answer
    assert "Image 1 wears glasses." in answer and "Image 2 wears a dark jacket." in answer


@pytest.mark.parametrize("failure", ["unavailable", "timeout"])
def test_comparison_keeps_visual_evidence_when_explanation_fails(monkeypatch, failure):
    from app.services.chat_service import ChatModelUnavailableError

    evidence = "Image 1 has glasses. Image 2 has a dark jacket."
    install_provider(monkeypatch, vision_result(answer=evidence))
    history = [
        ChatHistoryMessage(
            role="assistant", content="Saved image-generation provenance: earlier request"
        )
    ]

    async def unavailable(*args):
        raise (
            TimeoutError() if failure == "timeout" else ChatModelUnavailableError("private detail")
        )

    monkeypatch.setattr(ChatVisionService, "_complete_comparison", unavailable)
    answer = ChatVisionService().describe(
        b"one", "Compare both images", history, additional_images=[b"two"]
    )
    assert evidence in answer
    assert "temporarily unavailable" in answer
    assert "private detail" not in answer


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


def test_image_plan_uses_extracted_readings_and_full_text_completion(monkeypatch):
    provider = install_provider(monkeypatch, vision_result(answer="Body fat: 22.5%. BMI: 24.1."))
    calls = []

    async def complete(self, message, history, references):
        assert "energy needs are unknown" in self.SYSTEM_PROMPT
        assert "all seven days of meals" in self.SYSTEM_PROMPT
        calls.append((message, history, references))
        yield "## Diet plan\nBreakfast: oats.\n"
        yield "## Workout sessions\nMonday: strength; Tuesday: walking."

    monkeypatch.setattr("app.services.chat_vision_service.ChatService.stream_chat", complete)
    answer = ChatVisionService().describe(
        b"report", "Give me a diet plan and workout sessions based on my BMI image"
    )
    assert "Diet plan" in answer and "Workout sessions" in answer
    assert "Body fat: 22.5%. BMI: 24.1." in calls[0][2][0].content
    assert "starter example" not in calls[0][2][0].content
    assert "transcribe" in provider.calls[0]["system_prompt"]
    assert "Do not infer health conditions" in provider.calls[0]["system_prompt"]


def test_unreadable_plan_image_does_not_fabricate_measurements(monkeypatch):
    install_provider(monkeypatch, vision_result())
    monkeypatch.setattr(
        "app.services.chat_vision_service.ChatService.chat",
        lambda *a, **k: pytest.fail("No evidence"),
    )
    assert (
        ChatVisionService().describe(b"report", "Give me a diet plan")
        == ChatVisionService.EMPTY_RESPONSE_MESSAGE
    )


def test_production_prepares_nvidia_compatible_jpeg_even_with_ollama_dev_override(
    monkeypatch,
):
    provider = install_provider(monkeypatch, vision_result(answer="description"))
    captured = {}

    def prepare(image_bytes, *, max_dimension=None, force_jpeg=False):
        captured.update(
            image_bytes=image_bytes,
            max_dimension=max_dimension,
            force_jpeg=force_jpeg,
        )
        return b"jpeg-image"

    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr("app.services.chat_vision_service.prepare_vision_image", prepare)

    assert ChatVisionService().describe(b"source-image", "Describe it") == "description"
    assert captured == {
        "image_bytes": b"source-image",
        "max_dimension": settings.NVIDIA_VISION_MAX_DIMENSION,
        "force_jpeg": True,
    }
    assert provider.calls[0]["encoded_image"] == base64.b64encode(b"jpeg-image").decode("ascii")


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
