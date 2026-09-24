"""Transport-level checks for long answers, cancellation and persistent chat history."""

import asyncio
import json

import httpx
import pytest

from app.config import settings
from app.services.chat_service import (
    ChatModelUnavailableError,
    ChatService,
    _NvidiaFallbackError,
    _OutputLimitReached,
)


def install_streams(monkeypatch, responses, provider="nvidia", continuations=3):
    real_client = httpx.AsyncClient
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        current = responses[len(requests) - 1]
        if isinstance(current, int):
            return httpx.Response(current)
        if isinstance(current, dict):
            return httpx.Response(200, text=f"data: {json.dumps(current)}\n\n")
        content, reason = current
        if provider == "nvidia":
            events = [
                {"choices": [{"delta": {"content": content}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": reason}]},
            ]
            encoded = (
                "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"
            )
        else:
            encoded = (
                json.dumps({"message": {"content": content}, "done": True, "done_reason": reason})
                + "\n"
            )
        return httpx.Response(200, text=encoded)

    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "LLM_PROVIDER", provider)
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "unit-test-only")
    monkeypatch.setattr(settings, "CHAT_MAX_CONTINUATIONS", continuations)
    monkeypatch.setattr(
        "app.services.chat_service.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    return requests


def collect(chunks):
    async def run():
        async for chunk in ChatService().stream_chat("Compare the devices", [], []):
            chunks.append(chunk)

    asyncio.run(run())


@pytest.mark.parametrize("provider", ["nvidia", "ollama"])
def test_truncated_table_continues_in_place_without_a_second_heading(monkeypatch, provider):
    partial = (
        "## Comparison\n\n| Feature | Device A | Device B |\n|---|---|---|\n| Refresh rate | 90"
    )
    continuation = " Hz | 120 Hz |\n\nThe main tradeoff is display quality."
    calls = install_streams(monkeypatch, [(partial, "length"), (continuation, "stop")], provider)
    chunks = []
    collect(chunks)
    assert "".join(chunks) == partial + continuation
    assert len(calls) == 2
    assert calls[1]["messages"][:-2] == calls[0]["messages"]
    assert calls[1]["messages"][-2] == {"role": "assistant", "content": partial}
    assert "last character" in calls[1]["messages"][-1]["content"]
    assert "Do not repeat" in calls[1]["messages"][-1]["content"]
    assert chunks[0] == partial


def test_multiple_continuations_keep_one_accumulated_answer_and_original_context(monkeypatch):
    calls = install_streams(
        monkeypatch, [("First. ", "length"), ("Second. ", "length"), ("Last.", "stop")]
    )
    chunks = []
    collect(chunks)
    assert "".join(chunks) == "First. Second. Last."
    assert calls[2]["messages"][-2]["content"] == "First. Second. "
    assert len(calls[2]["messages"]) == len(calls[0]["messages"]) + 2


def test_provider_error_inside_http_200_retries_before_showing_any_text(monkeypatch):
    calls = install_streams(
        monkeypatch,
        [{"error": {"message": "temporarily unavailable"}}, ("Complete answer.", "stop")],
    )
    monkeypatch.setattr(ChatService, "NVIDIA_RETRY_DELAYS", (0, 0))
    chunks = []
    collect(chunks)
    assert "".join(chunks) == "Complete answer."
    assert calls[0] == calls[1]


def test_continuation_failure_does_not_restart_with_the_fallback_provider(monkeypatch):
    calls = install_streams(monkeypatch, [("Visible partial", "length"), 503, 503, 503])
    monkeypatch.setattr(ChatService, "NVIDIA_RETRY_DELAYS", (0, 0))

    async def forbidden(self, body):
        raise AssertionError("Cannot switch provider after showing output")
        yield ""

    monkeypatch.setattr(ChatService, "_stream_ollama", forbidden)
    chunks = []
    with pytest.raises(ChatModelUnavailableError, match="interrupted"):
        collect(chunks)
    assert chunks == ["Visible partial"]
    assert len(calls) == 4


def test_empty_failed_continuation_retries_same_request_without_repeating_text(monkeypatch):
    calls = install_streams(monkeypatch, [("Partial ", "length"), 503, ("completed.", "stop")])
    monkeypatch.setattr(ChatService, "NVIDIA_RETRY_DELAYS", (0, 0))
    chunks = []
    collect(chunks)
    assert "".join(chunks) == "Partial completed."
    assert len(calls) == 3
    assert calls[1] == calls[2]


def test_connection_loss_after_visible_text_resumes_from_last_character(monkeypatch):
    calls = []
    chunks = []

    async def provider(body):
        calls.append(body)
        yield ["First. ", "More. ", "Finished."][len(calls) - 1]
        if len(calls) == 1:
            raise _OutputLimitReached()
        if len(calls) == 2:
            raise _NvidiaFallbackError("lost connection")

    async def run():
        async for chunk in ChatService()._stream_with_continuations({"messages": []}, provider):
            chunks.append(chunk)

    monkeypatch.setattr(settings, "CHAT_MAX_CONTINUATIONS", 3)
    asyncio.run(run())
    assert len(calls) == 3
    assert "".join(chunks) == "First. More. Finished."
    assert calls[2]["messages"][-2]["content"] == "First. More. "


def test_continuation_attempts_are_bounded_and_partial_text_is_preserved(monkeypatch):
    calls = install_streams(
        monkeypatch, [("First. ", "length"), ("More.", "length")], continuations=1
    )
    chunks = []
    with pytest.raises(ChatModelUnavailableError, match="still incomplete"):
        collect(chunks)
    assert "".join(chunks) == "First. More."
    assert len(calls) == 2


def test_output_limit_without_visible_progress_does_not_loop_or_fallback(monkeypatch):
    calls = install_streams(monkeypatch, [("", "length")])
    with pytest.raises(ChatModelUnavailableError, match="without adding"):
        collect([])
    assert len(calls) == 1


def test_empty_continuation_is_not_reported_as_a_completed_answer(monkeypatch):
    calls = install_streams(monkeypatch, [("Unfinished answer", "length"), ("", "stop")])
    chunks = []
    with pytest.raises(ChatModelUnavailableError, match="without adding"):
        collect(chunks)
    assert chunks == ["Unfinished answer"]
    assert len(calls) == 2


def test_generator_close_stops_a_continuation_and_closes_its_provider(monkeypatch):
    closed = []
    calls = []

    async def provider(body):
        calls.append(body)
        try:
            yield "partial" if len(calls) == 1 else "continued"
            if len(calls) == 1:
                raise _OutputLimitReached()
            yield "not consumed"
        finally:
            closed.append(len(calls))

    async def run():
        stream = ChatService()._stream_with_continuations({"messages": []}, provider)
        assert await anext(stream) == "partial"
        assert await anext(stream) == "continued"
        await stream.aclose()

    monkeypatch.setattr(settings, "CHAT_MAX_CONTINUATIONS", 3)
    asyncio.run(run())
    assert len(calls) == 2
    assert closed == [1, 2]


@pytest.mark.parametrize("provider", ["nvidia", "ollama"])
def test_nonstream_chat_also_returns_the_complete_answer(monkeypatch, provider):
    calls = []
    outputs = [("A partial ", "length"), ("answer.", "stop")]

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        content, reason = outputs.pop(0)
        data = (
            {"choices": [{"message": {"content": content}, "finish_reason": reason}]}
            if provider == "nvidia"
            else {"message": {"content": content}, "done_reason": reason}
        )
        return httpx.Response(200, json=data, request=httpx.Request("POST", url))

    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "LLM_PROVIDER", provider)
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "unit-test-only")
    monkeypatch.setattr("app.services.chat_service.requests.post", post)
    assert ChatService().chat("Explain this", [], []) == "A partial answer."
    assert len(calls) == 2
    assert calls[1]["messages"][-2]["content"] == "A partial "


def test_stream_api_persists_one_completed_answer_after_continuation(client, monkeypatch):
    credentials = {"email": "continuation-test@example.com", "password": "test-password"}
    client.post("/users/", json={**credentials, "fullname": "Continuation verification"})
    token = client.post("/users/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    install_streams(monkeypatch, [("Part one. ", "length"), ("Part two.", "stop")])
    response = client.post("/chat/stream", json={"message": "Explain this"}, headers=headers)
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1] == {"type": "done"}
    assert not any(event["type"] == "error" for event in events)
    history = client.get(f"/chat/sessions/{events[0]['session_id']}", headers=headers).json()
    assert [item["content"] for item in history["messages"]] == [
        "Explain this",
        "Part one. Part two.",
    ]
