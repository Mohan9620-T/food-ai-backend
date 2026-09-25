import asyncio
import base64
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.api import chat_images
from app.config import settings
from app.models.chat import ChatDocumentAttachment, ChatMessageRecord
from app.services import image_generation_service as generation


def headers(client, label="image-owner"):
    credentials = {"email": f"{label}@example.com", "password": "image-test-password"}
    assert client.post("/users/", json={**credentials, "fullname": label}).status_code == 200
    login = client.post("/users/login", json=credentials)
    return {"Authorization": "Bearer " + login.json()["access_token"]}


def transport(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(settings, "ENABLE_IMAGE_GENERATION", True)
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr(
        generation.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.parametrize(
    "prompt,ratio,dimensions",
    [
        ("Create an image of a robot", "auto", (1024, 1024)),
        ("A vertical portrait 9:16", "auto", (768, 1344)),
        ("A landscape illustration", "auto", (1344, 768)),
        ("A robot", "16:9", (1344, 768)),
    ],
)
def test_provider_returns_valid_png_without_truncating_prompt(
    monkeypatch, valid_png_bytes, prompt, ratio, dimensions
):
    def handler(request):
        body = json.loads(request.content)
        assert body["prompt"] == prompt
        assert (body["width"], body["height"]) == dimensions
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "artifacts": [
                    {
                        "base64": base64.b64encode(valid_png_bytes).decode(),
                        "finishReason": "SUCCESS",
                    }
                ]
            },
        )

    transport(monkeypatch, handler)
    result = asyncio.run(generation.ImageGenerationService().generate(prompt, ratio))
    assert result.startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "status,expected", [(401, 503), (403, 503), (429, 429), (422, 422), (400, 422), (500, 503)]
)
def test_provider_errors_are_safe_and_never_retried(monkeypatch, status, expected):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"secret": "private provider detail"})

    transport(monkeypatch, handler)
    with pytest.raises(generation.ImageGenerationError) as error:
        asyncio.run(generation.ImageGenerationService().generate("Robot chef"))
    assert error.value.status_code == expected
    assert "private provider detail" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"artifacts": []},
        {"artifacts": [{"base64": "not base64"}]},
        {"artifacts": [{"base64": "aGVsbG8="}]},
        {"artifacts": [{"finishReason": "CONTENT_FILTERED", "base64": ""}]},
    ],
)
def test_invalid_or_filtered_results_never_become_attachments(monkeypatch, payload):
    transport(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with pytest.raises(generation.ImageGenerationError):
        asyncio.run(generation.ImageGenerationService().generate("Robot chef"))


def test_timeout_and_oversized_responses(monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("timeout")

    transport(monkeypatch, timeout)
    with pytest.raises(generation.ImageGenerationError) as error:
        asyncio.run(generation.ImageGenerationService().generate("Robot chef"))
    assert error.value.status_code == 504
    monkeypatch.setattr(generation, "MAX_RESPONSE_BYTES", 2)
    transport(monkeypatch, lambda request: httpx.Response(200, content=b"too large"))
    with pytest.raises(generation.ImageGenerationError):
        asyncio.run(generation.ImageGenerationService().generate("Robot chef"))


def test_image_persists_with_history_and_private_download(
    client, db_session, monkeypatch, valid_png_bytes
):
    owner = headers(client)
    other = headers(client, "other-owner")
    generate = AsyncMock(return_value=valid_png_bytes)
    monkeypatch.setattr(chat_images.service, "generate", generate)
    response = client.post(
        "/chat/images", headers=owner, json={"message": "Generate an image of a robot chef"}
    )
    assert response.status_code == 200
    result = response.json()
    attachment_id = result["attachment"]["id"]
    session_id = result["session_id"]
    history = client.get(f"/chat/sessions/{session_id}", headers=owner).json()
    assert len(history["messages"]) == 2
    assert history["messages"][-1]["attachments"][0]["id"] == attachment_id
    attachment = db_session.get(ChatDocumentAttachment, attachment_id)
    assert attachment.generation_metadata["prompt"] == "Generate an image of a robot chef"
    assert (
        client.get(f"/chat/documents/{attachment_id}/download", headers=owner).content
        == valid_png_bytes
    )
    assert client.get(f"/chat/documents/{attachment_id}/download", headers=other).status_code == 404
    assert (
        client.post(
            "/chat/images",
            headers=other,
            json={"message": "Create a logo", "session_id": session_id},
        ).status_code
        == 404
    )
    assert generate.await_count == 1
    assert client.post("/chat/images", json={"message": "Create a logo"}).status_code == 401


def test_failed_generation_does_not_save_fake_success(client, db_session, monkeypatch):
    owner = headers(client)
    monkeypatch.setattr(
        chat_images.service,
        "generate",
        AsyncMock(side_effect=generation.ImageGenerationError("Image generation timed out", 504)),
    )
    response = client.post(
        "/chat/images", headers=owner, json={"message": "Create an image of a cat"}
    )
    assert response.status_code == 504
    assert db_session.query(ChatMessageRecord).count() == 0
    assert db_session.query(ChatDocumentAttachment).count() == 0


def test_capabilities_missing_key_and_input_validation(client, monkeypatch):
    owner = headers(client)
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "")
    with pytest.raises(generation.ImageGenerationError):
        asyncio.run(generation.ImageGenerationService().generate("A cat"))
    generate = AsyncMock()
    monkeypatch.setattr(chat_images.service, "generate", generate)
    result = client.post(
        "/chat/images", headers=owner, json={"message": "you can generate or create the image?"}
    )
    assert result.status_code == 200
    assert "not configured" in result.json()["response"]
    assert result.json()["attachment"] is None
    generate.assert_not_awaited()
    for message in ["", "   ", "a" * 10001]:
        assert (
            client.post("/chat/images", headers=owner, json={"message": message}).status_code == 422
        )
    assert (
        generation.image_request_help("create an image")
        == "Describe the image you want: its subject, setting, and style."
    )
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr(settings, "ENABLE_IMAGE_GENERATION", True)
    assert "download button" in generation.image_request_help("Can you generate images?")
