import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

from app.api import chat_images
from app.config import settings
from app.services.chat_service import ChatModelUnavailableError, ChatService, _NvidiaFallbackError
from app.services.chat_vision_service import ChatVisionService
from app.services.image_edit_intent import image_edit_response
from app.services.image_generation_service import GeneratedImage
from app.services.image_parser_service import VisionModelUnavailableError
from app.services.provider_policy import ollama_fallback_enabled
from app.services.vision_providers.failover_provider import FailoverVisionProvider
from app.services.vision_providers.nvidia_provider import VisionRequestDeclinedError
from tests.test_image_generation import headers


@pytest.mark.parametrize(
    "environment,configured,expected",
    [
        ("production", None, False),
        ("development", None, True),
        ("production", True, True),
        ("development", False, False),
    ],
)
def test_fallback_requires_a_provisioned_production_server(
    monkeypatch, environment, configured, expected
):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", environment)
    monkeypatch.setattr(settings, "ENABLE_OLLAMA_FALLBACK", configured)
    assert ollama_fallback_enabled() is expected


@pytest.mark.parametrize(
    "failure", [VisionModelUnavailableError("NVIDIA unavailable"), ValueError("bad JSON")]
)
def test_production_vision_never_contacts_absent_local_server(monkeypatch, failure):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ENABLE_OLLAMA_FALLBACK", None)
    nvidia, ollama = Mock(), Mock()
    nvidia.infer.side_effect = failure
    with pytest.raises(VisionModelUnavailableError, match="temporarily unavailable"):
        FailoverVisionProvider(nvidia, ollama).infer("system", "question", "image")
    ollama.infer.assert_not_called()


def test_declined_vision_request_never_uses_fallback(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_OLLAMA_FALLBACK", True)
    nvidia, ollama = Mock(), Mock()
    nvidia.infer.side_effect = VisionRequestDeclinedError("Request declined")
    with pytest.raises(VisionRequestDeclinedError):
        FailoverVisionProvider(nvidia, ollama).infer("system", "question", "image")
    ollama.infer.assert_not_called()


@pytest.mark.parametrize(
    "method", ["chat", "complete_chat", "stream_chat", "complete_follow_up_suggestions"]
)
def test_all_production_text_paths_stop_before_unconfigured_fallback(monkeypatch, method):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ENABLE_OLLAMA_FALLBACK", None)
    service = ChatService()
    unavailable = _NvidiaFallbackError("NVIDIA unavailable")
    monkeypatch.setattr(service, "_chat_with_nvidia", Mock(side_effect=unavailable))
    monkeypatch.setattr(service, "_complete_with_nvidia", AsyncMock(side_effect=unavailable))
    local_sync = Mock(side_effect=AssertionError("Do not contact localhost"))
    local_async = AsyncMock(side_effect=AssertionError("Do not contact localhost"))
    monkeypatch.setattr(service, "_chat_with_ollama", local_sync)
    monkeypatch.setattr(service, "_complete_with_ollama", local_async)
    monkeypatch.setattr(service, "_stream_ollama", local_sync)

    async def failed_stream(_body):
        raise unavailable
        yield  # pragma: no cover

    monkeypatch.setattr(service, "_stream_nvidia", failed_stream)

    async def stream():
        return [chunk async for chunk in service.stream_chat("Hello", [], [])]

    with pytest.raises(ChatModelUnavailableError, match="temporarily unavailable"):
        if method == "chat":
            service.chat("Hello", [], [])
        elif method == "stream_chat":
            asyncio.run(stream())
        elif method == "complete_chat":
            asyncio.run(service.complete_chat("Hello", [], []))
        else:
            asyncio.run(service.complete_follow_up_suggestions("Hello", "Hi"))
    local_sync.assert_not_called()
    local_async.assert_not_called()


@pytest.mark.parametrize(
    "question",
    [
        "enhance the cinematic atmosphere around the actor vijay",
        "please change this image background",
        "make it brighter",
    ],
)
@pytest.mark.parametrize("endpoint", ["/chat/", "/chat/stream"])
def test_edit_followup_after_reload_finishes_without_vision_or_web(
    client, monkeypatch, valid_png_bytes, question, endpoint
):
    owner = headers(client)
    monkeypatch.setattr(chat_images, "resolve_image_subject", AsyncMock(return_value=None))
    monkeypatch.setattr(
        chat_images.service,
        "generate",
        AsyncMock(
            return_value=GeneratedImage(
                valid_png_bytes, "Actor Vijay in cinematic light", "1:1", 2, 2
            )
        ),
    )
    created = client.post(
        "/chat/images", headers=owner, json={"message": "Create a cinematic image of actor Vijay"}
    ).json()
    session_id = created["session_id"]
    assert client.get(f"/chat/sessions/{session_id}", headers=owner).status_code == 200
    provider = Mock(side_effect=AssertionError("Editing must not call image analysis"))
    monkeypatch.setattr("app.services.chat_vision_service.get_vision_provider", provider)
    monkeypatch.setattr(
        ChatService, "_maybe_web_search", Mock(side_effect=AssertionError("No web lookup"))
    )
    response = client.post(
        f"{endpoint}?session_id={session_id}", headers=owner, json={"message": question}
    )
    assert response.status_code == 200
    if endpoint.endswith("stream"):
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[-1]["type"] == "done"
        answer = "".join(event.get("content", "") for event in events)
    else:
        answer = response.json()["response"]
    assert "cannot edit the existing image" in answer
    assert "Create image" in answer
    assert "Response interrupted" not in answer
    restored = client.get(f"/chat/sessions/{session_id}", headers=owner).json()
    assert restored["messages"][-1]["content"] == answer
    provider.assert_not_called()


@pytest.mark.parametrize(
    "question",
    [
        "Describe the cinematic atmosphere",
        "How can I enhance this image?",
        "Improve my image prompt",
        "Change the background in my CSS",
        "Make a PDF of the image",
    ],
)
def test_edit_routing_leaves_analysis_and_other_tasks_alone(question):
    assert image_edit_response(question) is None


def test_uploaded_image_edit_does_not_require_inference(monkeypatch, valid_png_bytes):
    provider = Mock(side_effect=AssertionError("No inference"))
    monkeypatch.setattr("app.services.chat_vision_service.get_vision_provider", provider)
    answer = ChatVisionService().describe(valid_png_bytes, "Enhance the cinematic lighting")
    assert "cannot edit" in answer
    provider.assert_not_called()


def test_live_and_persisted_stream_failure_do_not_expose_server_setup(client, monkeypatch):
    owner = headers(client)

    async def failed(self, *args, **kwargs):
        yield "Partial useful answer."
        raise ChatModelUnavailableError(
            "Chat model unavailable: configured model 'qwen3:8b'. Confirm Ollama is running."
        )

    monkeypatch.setattr(ChatService, "stream_chat", failed)
    response = client.post("/chat/stream", headers=owner, json={"message": "Hello"})
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1]["type"] == "error"
    assert "temporarily unavailable" in events[-1]["message"]
    assert "Ollama" not in response.text and "qwen" not in response.text
    restored = client.get(f"/chat/sessions/{events[0]['session_id']}", headers=owner).json()
    answer = restored["messages"][-1]["content"]
    assert answer.startswith("Partial useful answer.")
    assert "Response interrupted" in answer and "qwen" not in answer


def test_unexpected_image_failure_terminates_stream_and_saves_retry_notice(
    client, monkeypatch, valid_png_bytes
):
    owner = headers(client)
    monkeypatch.setattr(chat_images, "resolve_image_subject", AsyncMock(return_value=None))
    monkeypatch.setattr(
        chat_images.service,
        "generate",
        AsyncMock(return_value=GeneratedImage(valid_png_bytes, "A robot", "1:1", 2, 2)),
    )
    created = client.post("/chat/images", headers=owner, json={"message": "Draw a robot"}).json()
    monkeypatch.setattr(
        ChatVisionService,
        "describe",
        Mock(side_effect=ValueError("Unexpected private response payload")),
    )
    response = client.post(
        f"/chat/stream?session_id={created['session_id']}",
        headers=owner,
        json={"message": "Describe this image"},
    )
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1]["type"] == "error"
    assert "Please retry" in events[-1]["message"]
    assert "private response payload" not in response.text
    restored = client.get(f"/chat/sessions/{created['session_id']}", headers=owner).json()
    assert restored["messages"][-1]["content"].startswith("Response interrupted:")
