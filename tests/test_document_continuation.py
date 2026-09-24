import asyncio
import json

import httpx
import pytest

from app.config import settings
from app.services.chat_service import ChatModelUnavailableError, ChatService


def install_completions(monkeypatch, segments, provider):
    calls = []
    real_client = httpx.AsyncClient

    def respond(request):
        calls.append(json.loads(request.content))
        content, reason = segments[len(calls) - 1]
        payload = (
            {"choices": [{"message": {"content": content}, "finish_reason": reason}]}
            if provider == "nvidia"
            else {"message": {"content": content}, "done_reason": reason, "done": True}
        )
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "LLM_PROVIDER", provider)
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "unit-test-only")
    monkeypatch.setattr(settings, "CHAT_MAX_CONTINUATIONS", 3)
    monkeypatch.setattr(
        "app.services.chat_service.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    return calls


@pytest.mark.parametrize("provider", ["nvidia", "ollama"])
def test_atomic_document_json_retries_complete_structure_with_larger_budget(monkeypatch, provider):
    parts = [
        '{"title":"Inventory","rows":[["Fresh',
        '{"title":"Inventory","rows":[["Fresh milk",1.50],',
        '{"title":"Inventory","rows":[["Fresh milk",1.50],["Rice",2.00]]}',
    ]
    calls = install_completions(
        monkeypatch, list(zip(parts, ["length", "length", "stop"])), provider
    )
    output = asyncio.run(
        ChatService().complete_chat("Create an inventory JSON document", [], [], max_tokens=128)
    )
    assert json.loads(output) == {
        "title": "Inventory",
        "rows": [["Fresh milk", 1.5], ["Rice", 2.0]],
    }
    assert len(calls) == 3
    assert calls[1]["messages"] == calls[0]["messages"] == calls[2]["messages"]
    budgets = [
        call["max_tokens"] if provider == "nvidia" else call["options"]["num_predict"]
        for call in calls
    ]
    assert budgets == [128, 4096, 8192]


@pytest.mark.parametrize("provider", ["nvidia", "ollama"])
def test_atomic_prose_continues_in_place_without_losing_whitespace(monkeypatch, provider):
    calls = install_completions(
        monkeypatch, [("A table\n| Rice | 2", "length"), (".00 |\n\nComplete.", "stop")], provider
    )
    output = asyncio.run(
        ChatService().complete_chat("Explain the inventory", [], [], max_tokens=128)
    )
    assert output == "A table\n| Rice | 2.00 |\n\nComplete."
    assert calls[1]["messages"][-2]["content"] == "A table\n| Rice | 2"


@pytest.mark.parametrize("provider", ["nvidia", "ollama"])
def test_unfinished_document_is_never_returned_as_valid_output(monkeypatch, provider):
    calls = install_completions(
        monkeypatch, [("Incomplete ", "length"), ("still incomplete", "length")], provider
    )
    monkeypatch.setattr(settings, "CHAT_MAX_CONTINUATIONS", 1)
    with pytest.raises(ChatModelUnavailableError, match="still incomplete"):
        asyncio.run(ChatService().complete_chat("Create a report", [], []))
    assert len(calls) == 2


def test_document_continuation_cancellation_stops_provider_work(monkeypatch):
    from app.services.chat_service import _OutputLimitReached

    calls = []
    closed = []

    async def provider(body):
        calls.append(body)
        if len(calls) == 1:
            raise _OutputLimitReached('{"title":"')
        try:
            await asyncio.sleep(10)
        finally:
            closed.append(True)

    async def run():
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.02):
                await ChatService()._complete_with_continuations({"messages": []}, provider)

    monkeypatch.setattr(settings, "CHAT_MAX_CONTINUATIONS", 3)
    asyncio.run(run())
    assert len(calls) == 2 and closed == [True]
