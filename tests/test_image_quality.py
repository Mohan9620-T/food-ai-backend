"""Quality requests retain detail, use repeatable settings and preserve provenance."""

import asyncio
import base64
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.api import chat_images
from app.models.chat import ChatDocumentAttachment
from app.services import image_generation_service as generation
from tests.test_image_generation import headers, transport


def image_response(data):
    return httpx.Response(200, json={"artifacts": [{"base64": base64.b64encode(data).decode()}]})


def test_quality_model_preserves_long_original_without_text_compression(
    monkeypatch, valid_png_bytes
):
    original = (
        "Photorealistic live-action adult fictional traveler sleeping alone in a sci-fi cabin. "
        "One visible person; the photographer stays outside the frame. "
        + "Natural skin, warm rim light, rumpled quilt, neon window. " * 25
        + "9:16 vertical. Keep the whole body in frame, no extra people or limbs."
    )
    calls = []

    def handler(request):
        assert str(request.url) == generation.IMAGE_ENDPOINT
        body = json.loads(request.content)
        calls.append(body)
        assert body["prompt"].endswith(original)
        assert body["steps"] == 50
        assert body["cfg_scale"] == 3.5
        assert (body["width"], body["height"]) == (768, 1344)
        assert body["mode"] == "base"
        assert body["samples"] == 1
        assert 0 < body["seed"] <= generation.MAX_IMAGE_SEED
        return image_response(valid_png_bytes)

    transport(monkeypatch, handler, model=generation.IMAGE_MODEL)
    service = generation.ImageGenerationService()
    first = asyncio.run(service.generate(original))
    second = asyncio.run(service.generate(original))
    assert calls[0] == calls[1]  # No random seed or stochastic prompt rewrite.
    assert first.model == generation.IMAGE_MODEL
    assert first.seed == second.seed == calls[0]["seed"]
    assert first.steps == 50 and first.guidance == 3.5
    assert first.data.startswith(b"\x89PNG")


def test_explicit_seed_can_request_a_different_variation(monkeypatch, valid_png_bytes):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return image_response(valid_png_bytes)

    transport(monkeypatch, handler, model=generation.IMAGE_MODEL)
    service = generation.ImageGenerationService()
    first = asyncio.run(service.generate("A blue robot", seed=41))
    second = asyncio.run(service.generate("A blue robot", seed=42))
    assert [first.seed, second.seed] == [41, 42]
    assert calls[0]["prompt"] == calls[1]["prompt"] == "A blue robot"
    assert [c["seed"] for c in calls] == [41, 42]


def test_seed_normalizes_whitespace_and_changes_with_subject_model_or_framing():
    seed = generation.generation_seed("A blue robot", generation.IMAGE_MODEL, "1:1")
    assert seed == generation.generation_seed("A\nblue  robot", generation.IMAGE_MODEL, "1:1")
    assert seed != generation.generation_seed("A red robot", generation.IMAGE_MODEL, "1:1")
    assert seed != generation.generation_seed("A blue robot", generation.IMAGE_MODEL, "9:16")
    assert seed != generation.generation_seed("A blue robot", "other-model", "1:1")


def test_fast_model_keeps_its_own_supported_inference_settings(monkeypatch, valid_png_bytes):
    def handler(request):
        assert str(request.url).endswith("/flux.2-klein-4b")
        body = json.loads(request.content)
        assert body["steps"] == 4 and body["seed"] > 0
        assert "cfg_scale" not in body and "mode" not in body
        return image_response(valid_png_bytes)

    transport(monkeypatch, handler)
    result = asyncio.run(generation.ImageGenerationService().generate("A robot"))
    assert result.model == "black-forest-labs/flux.2-klein-4b"
    assert result.steps == 4 and result.guidance is None


@pytest.mark.parametrize("max_chars", [800, 10_000])
def test_incomplete_but_valid_json_is_retried_before_image_generation(
    monkeypatch, valid_png_bytes, max_chars
):
    # Reproduce a provider string cut off exactly at the former schema limit.
    truncated = "An adult traveler. " + "x" * (max_chars - 22) + "rum"
    assert len(truncated) == max_chars
    original = "A scene with one traveler. " * 450
    calls = []
    prepared = "One adult traveler asleep under a quilt, neon sci-fi cabin, candid photo."

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert request.url.path.endswith("/chat/completions")
        assert body["messages"][1]["content"] == original
        schema = body["response_format"]["json_schema"]["schema"]
        assert "maxLength" not in schema["properties"]["prompt"]["anyOf"][0]
        text = truncated if len(calls) == 1 else prepared
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"prompt": text, "error": None})},
                    }
                ]
            },
        )

    transport(monkeypatch, handler)
    result = asyncio.run(
        generation.ImageGenerationService()._prepare_prompt(original, max_chars=max_chars)
    )
    assert result == prepared and len(calls) == 2
    assert "incomplete_description" in calls[1]["messages"][0]["content"]


@pytest.mark.parametrize(
    "prompt",
    [
        "An anime illustration of two friends in a cabin.",
        "A cartoon, not photorealistic, of three travelers.",
        "A dragon in a painted fantasy landscape.",
        "No live-action. Draw a character.",
    ],
)
def test_photography_guidance_does_not_override_other_styles(prompt):
    assert generation.photography_prompt(prompt) == prompt


def test_photography_guidance_keeps_explicit_multiple_subjects_and_visible_camera():
    prompt = "Photorealistic photo of two actors, with a third person visibly holding a camera."
    prepared = generation.photography_prompt(prompt)
    assert prepared.endswith(prompt)
    assert "unless explicitly requested as a visible subject" in prepared
    assert "exactly one" not in prepared


def test_explicit_wide_ratio_overrides_the_word_portrait(monkeypatch, valid_png_bytes):
    def handler(request):
        body = json.loads(request.content)
        assert (body["width"], body["height"]) == (1344, 768)
        return image_response(valid_png_bytes)

    transport(monkeypatch, handler, model=generation.IMAGE_MODEL)
    result = asyncio.run(generation.ImageGenerationService().generate("A group portrait, 16:9"))
    assert result.aspect_ratio == "16:9"


def test_unknown_model_is_not_sent_to_an_arbitrary_endpoint(monkeypatch):
    def unexpected(request):
        raise AssertionError("No network request should be made")

    transport(monkeypatch, unexpected, model="https://untrusted.example/model")
    with pytest.raises(generation.ImageGenerationError, match="not supported"):
        asyncio.run(generation.ImageGenerationService().generate("A robot"))


def test_history_records_actual_model_seed_and_settings(
    client, db_session, monkeypatch, valid_png_bytes
):
    owner = headers(client, "quality-owner")
    generate = AsyncMock(
        return_value=generation.GeneratedImage(
            valid_png_bytes, "A robot", "1:1", 2, 2, generation.IMAGE_MODEL, 12345, 50, 3.5
        )
    )
    monkeypatch.setattr(chat_images.service, "generate", generate)
    response = client.post(
        "/chat/images", headers=owner, json={"message": "A robot", "seed": 12345}
    )
    assert response.status_code == 200
    generate.assert_awaited_once_with("A robot", "auto", seed=12345)
    attachment = db_session.get(ChatDocumentAttachment, response.json()["attachment"]["id"])
    metadata = attachment.generation_metadata
    assert metadata["model"] == generation.IMAGE_MODEL
    assert metadata["seed"] == 12345 and metadata["steps"] == 50
    assert metadata["guidance"] == 3.5 and metadata["preparation_version"] == 2
    assert metadata["prompt_compacted"] is False
    restored = client.get(f"/chat/sessions/{response.json()['session_id']}", headers=owner)
    assert restored.json()["messages"][-1]["attachments"][0]["id"] == attachment.id


@pytest.mark.parametrize("seed", [0, -1, 2147483648, 1.5, True, "123"])
def test_api_rejects_invalid_or_implicitly_random_seed(client, monkeypatch, seed):
    owner = headers(client, "seed-owner")
    generate = AsyncMock()
    monkeypatch.setattr(chat_images.service, "generate", generate)
    response = client.post("/chat/images", headers=owner, json={"message": "A robot", "seed": seed})
    assert response.status_code == 422
    generate.assert_not_awaited()
