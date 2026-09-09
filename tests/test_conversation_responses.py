"""Offline regressions for context delivery and complete conversational responses.

These verify the application protocol, not the quality of a live model's judgment.
"""

import asyncio
import json

import httpx
import pytest

from app.config import settings
from app.schemas.chat import ChatHistoryMessage
from app.schemas.vision_result import VisionResult
from app.services.chat_service import ChatModelUnavailableError, ChatService
from app.services.chat_vision_service import ChatVisionService
from app.services.conversation_guidance import CONVERSATION_GUIDANCE


def _install_nvidia_stream(monkeypatch, events):
    """Keep the real HTTPX streaming machinery but replace all network transport."""
    real_async_client = httpx.AsyncClient
    captured = []
    encoded = "".join(
        f"data: {event if isinstance(event, str) else json.dumps(event)}\n\n" for event in events
    )

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, text=encoded, headers={"Content-Type": "text/event-stream"})

    def client(**kwargs):
        return real_async_client(transport=httpx.MockTransport(respond), **kwargs)

    async def unexpected_ollama(self, body):
        raise AssertionError("A started NVIDIA answer must not switch providers")
        yield ""  # pragma: no cover - retain the async generator interface

    monkeypatch.setattr(settings, "LLM_PROVIDER", "nvidia")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "unit-test-key")
    monkeypatch.setattr("app.services.chat_service.httpx.AsyncClient", client)
    monkeypatch.setattr(ChatService, "_stream_ollama", unexpected_ollama)
    return captured


def _event(content=None, *, finish_reason=None, **delta):
    return {
        "choices": [
            {
                "delta": {"content": content, **delta},
                "finish_reason": finish_reason,
            }
        ]
    }


def _collect_stream(chunks):
    async def collect():
        async for chunk in ChatService().stream_chat("Hello", [], []):
            chunks.append(chunk)

    asyncio.run(collect())


@pytest.mark.parametrize("stream", [False, True])
def test_nvidia_output_budget_is_independent_of_local_model(monkeypatch, stream):
    monkeypatch.setattr(settings, "OLLAMA_CHAT_MAX_TOKENS", 256)
    monkeypatch.setattr(settings, "NVIDIA_CHAT_MAX_TOKENS", 1024)
    monkeypatch.setattr(settings, "NVIDIA_CHAT_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
    service = ChatService()
    _, local_body = service._build_request_body("Hello", [], [], stream=stream)

    hosted_body = service._nvidia_body(local_body, stream=stream)

    assert local_body["options"]["num_predict"] == 256
    assert hosted_body["max_tokens"] == 1024
    assert hosted_body["chat_template_kwargs"] == {"enable_thinking": False}
    assert hosted_body["stream"] is stream
    assert hosted_body["messages"] == local_body["messages"]


def test_nvidia_truncation_reports_incomplete_without_mixing_providers(monkeypatch):
    _install_nvidia_stream(
        monkeypatch,
        [_event("The first part. "), _event("Still writing", finish_reason="length"), "[DONE]"],
    )
    chunks = []

    with pytest.raises(ChatModelUnavailableError, match="output limit"):
        _collect_stream(chunks)

    assert "".join(chunks) == "The first part. Still writing"


@pytest.mark.parametrize("termination", [[_event(finish_reason="stop")], ["[DONE]"]])
def test_nvidia_normal_completion_ignores_usage_and_private_reasoning(monkeypatch, termination):
    _install_nvidia_stream(
        monkeypatch,
        [
            _event(reasoning_content="Internal reasoning must not be visible"),
            _event("Hello. "),
            {"choices": [], "usage": {"completion_tokens": 5}},
            _event("How can I help?"),
            *termination,
        ],
    )
    chunks = []

    _collect_stream(chunks)

    assert chunks == ["Hello. ", "How can I help?"]


def test_nvidia_eof_without_completion_is_interruption(monkeypatch):
    _install_nvidia_stream(monkeypatch, [_event("An unfinished sentence")])
    chunks = []

    with pytest.raises(ChatModelUnavailableError, match="interrupted"):
        _collect_stream(chunks)

    assert chunks == ["An unfinished sentence"]


def test_nvidia_invalid_content_after_tokens_is_interruption(monkeypatch):
    _install_nvidia_stream(
        monkeypatch, [_event("Valid text"), _event({"unexpected": "object"}), "[DONE]"]
    )
    chunks = []

    with pytest.raises(ChatModelUnavailableError, match="interrupted"):
        _collect_stream(chunks)

    assert chunks == ["Valid text"]


def test_synchronous_nvidia_does_not_accept_truncated_reply(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": "A cut-off answer"}, "finish_reason": "length"}]
            }

    def post(url, **kwargs):
        calls.append(url)
        return Response()

    monkeypatch.setattr(settings, "LLM_PROVIDER", "nvidia")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "unit-test-key")
    monkeypatch.setattr("app.services.chat_service.requests.post", post)

    with pytest.raises(ChatModelUnavailableError, match="output limit"):
        ChatService().chat("Hello", [], [])

    assert calls == [f"{settings.NVIDIA_API_BASE_URL}/chat/completions"]


def test_duplicate_current_turn_does_not_reduce_retained_context():
    history = [
        ChatHistoryMessage(role="user", content=f"detail {index}")
        for index in range(ChatService.HISTORY_MESSAGE_LIMIT)
    ]
    history.append(ChatHistoryMessage(role="user", content="What should I do?"))

    _, body = ChatService()._build_request_body("What should I do?", history, [], stream=True)

    user_messages = [item["content"] for item in body["messages"] if item["role"] == "user"]
    assert user_messages == [item.content for item in history]


def test_latest_correction_follows_recalled_preference_in_request():
    history = [ChatHistoryMessage(role="user", content="Please call me Master")]
    history.extend(
        ChatHistoryMessage(role="user", content=f"detail {index}")
        for index in range(ChatService.HISTORY_MESSAGE_LIMIT)
    )
    history.append(ChatHistoryMessage(role="user", content="Don't call me Master anymore"))

    _, body = ChatService()._build_request_body(
        "I need someone to listen", history, [], stream=True
    )

    contents = [item["content"] for item in body["messages"]]
    assert contents.index("Please call me Master") < contents.index("Don't call me Master anymore")
    assert contents[-1] == "I need someone to listen"


def test_personal_conversation_does_not_end_with_bug_report_instructions():
    _, body = ChatService()._build_request_body(
        "I do not want to call a helpline. Why did they leave me?", [], [], stream=True
    )

    final_instruction = body["messages"][-2]["content"]
    assert "Respond to what changed in this turn" in final_instruction
    assert "when a helpline was declined" in final_instruction
    assert "For formatting tasks:" not in final_instruction


def test_saved_session_context_beyond_twelve_messages_reaches_model(client, monkeypatch):
    credentials = {"email": "conversation-context@example.com", "password": "test-password"}
    client.post("/users/", json={"fullname": "Conversation User", **credentials})
    token = client.post("/users/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    session = client.post("/chat/sessions", json={"title": "Current"}, headers=headers).json()
    other = client.post("/chat/sessions", json={"title": "Other"}, headers=headers).json()
    messages = [{"sender": "user", "content": f"saved detail {index}"} for index in range(28)]
    client.post(
        f"/chat/sessions/{session['id']}/import", json={"messages": messages}, headers=headers
    )
    client.post(
        f"/chat/sessions/{other['id']}/import",
        json={"messages": [{"sender": "user", "content": "Other session private detail"}]},
        headers=headers,
    )
    captured = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "I hear you."}}

    def post(url, **kwargs):
        captured.append(kwargs["json"])
        return Response()

    monkeypatch.setattr("app.services.chat_service.requests.post", post)
    response = client.post(
        f"/chat/?session_id={session['id']}",
        json={"message": "What now?", "history": [{"role": "user", "content": "Browser detail"}]},
        headers=headers,
    )

    assert response.status_code == 200
    user_contents = [item["content"] for item in captured[0]["messages"] if item["role"] == "user"]
    assert user_contents == [item["content"] for item in messages[-24:]] + ["What now?"]


def test_shared_guidance_and_recent_context_reach_vision_provider(monkeypatch, valid_png_bytes):
    captured = []

    class Provider:
        def infer(self, **kwargs):
            captured.append(kwargs)
            return VisionResult(
                image_type="other", answer="I hear you.", items=[], uncertain_items=[]
            )

    monkeypatch.setattr("app.services.chat_vision_service.get_vision_provider", Provider)
    monkeypatch.setattr(settings, "CHAT_VISION_OCR_ENABLED", False)
    history = [ChatHistoryMessage(role="user", content="My partner left and I feel overwhelmed")]
    history.extend(
        ChatHistoryMessage(role="user", content=f"detail {index}") for index in range(14)
    )

    result = ChatVisionService().describe(valid_png_bytes, "What should I do now?", history)
    _, text_body = ChatService()._build_request_body(
        "What should I do now?", history, [], stream=True
    )

    assert result == "I hear you."
    assert CONVERSATION_GUIDANCE in captured[0]["system_prompt"]
    assert CONVERSATION_GUIDANCE in text_body["messages"][0]["content"]
    assert history[0].content in captured[0]["user_prompt"]
    assert captured[0]["user_prompt"].endswith("Latest question: What should I do now?")
