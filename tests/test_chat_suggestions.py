import asyncio
import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from app.api import chat_suggestions
from app.models.chat import ChatMessageRecord
from app.services.chat_service import ChatModelUnavailableError, ChatService, _NvidiaFallbackError
from app.services.chat_suggestion_service import ChatSuggestionService

OPTIONS = [
    {"label": "See an orbital example", "prompt": "Explain gravity using the Moon's orbit."},
    {"label": "Compare with magnetism", "prompt": "How does gravity differ from magnetism?"},
]


def login(client, email):
    client.post(
        "/users/", json={"fullname": "Suggestion Test", "email": email, "password": "test123456"}
    )
    token = client.post("/users/login", json={"email": email, "password": "test123456"}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def conversation(client, db_session, monkeypatch):
    headers = login(client, "suggestions@example.com")
    session_id = client.post("/chat/sessions", headers=headers, json={"title": "Gravity"}).json()[
        "id"
    ]
    db_session.add(
        ChatMessageRecord(session_id=session_id, sender="user", content="What is gravity?")
    )
    answer = ChatMessageRecord(
        session_id=session_id, sender="bot", content="Gravity attracts masses together."
    )
    db_session.add(answer)
    db_session.commit()
    service = ChatSuggestionService()
    model = AsyncMock(return_value=json.dumps({"suggestions": OPTIONS}))
    monkeypatch.setattr(service.model, "complete_follow_up_suggestions", model)
    monkeypatch.setattr(chat_suggestions, "service", service)
    return headers, session_id, answer, model


def request(client, conversation, **overrides):
    headers, session_id, answer, _ = conversation
    return client.post(
        f"/chat/sessions/{session_id}/suggestions",
        headers=overrides.get("headers", headers),
        json={
            "answer_hash": overrides.get(
                "answer_hash", hashlib.sha256(answer.content.encode()).hexdigest()
            )
        },
    )


def test_suggestions_use_saved_answer_and_cache_without_changing_history(
    client, db_session, conversation
):
    response = request(client, conversation)
    assert response.status_code == 200
    assert response.json() == {"message_id": conversation[2].id, "suggestions": OPTIONS}
    assert request(client, conversation).json() == response.json()
    conversation[3].assert_awaited_once_with(
        "What is gravity?", "Gravity attracts masses together."
    )
    assert db_session.query(ChatMessageRecord).count() == 2


def test_another_user_cannot_get_suggestions_or_cached_answer(client, conversation):
    assert request(client, conversation).status_code == 200
    other = login(client, "other-suggestions@example.com")
    assert request(client, conversation, headers=other).status_code == 404
    assert request(client, conversation, headers={}).status_code in (401, 403)
    conversation[3].assert_awaited_once()


def test_stale_hash_and_unfinished_turn_are_rejected(client, db_session, conversation):
    assert request(client, conversation, answer_hash="0" * 64).status_code == 409
    assert request(client, conversation, answer_hash="invalid").status_code == 422
    db_session.add(
        ChatMessageRecord(session_id=conversation[1], sender="user", content="New question")
    )
    db_session.commit()
    assert request(client, conversation).status_code == 409
    conversation[3].assert_not_awaited()


@pytest.mark.parametrize(
    "status", ["clarification_required", "ready_for_review", "partial", "failed"]
)
def test_no_generic_choices_during_document_clarification(client, db_session, conversation, status):
    conversation[2].automation = {"response": {"status": status}}
    db_session.commit()
    assert request(client, conversation).json()["suggestions"] == []
    conversation[3].assert_not_awaited()


def test_interrupted_answer_has_no_suggestions(client, db_session, conversation):
    conversation[2].content += "\nResponse interrupted: cancelled"
    db_session.commit()
    assert request(client, conversation).json()["suggestions"] == []
    conversation[3].assert_not_awaited()


@pytest.mark.parametrize(
    "failure", [TimeoutError(), ChatModelUnavailableError("Offline"), ValueError("Invalid JSON")]
)
def test_optional_model_failure_preserves_successful_chat(client, conversation, failure):
    conversation[3].side_effect = failure
    response = request(client, conversation)
    assert response.status_code == 200
    assert len(response.json()["suggestions"]) == 2


def test_parse_rejects_malformed_duplicate_and_other_choices():
    rows = [
        {"label": "Other", "prompt": "Other"},
        {"label": "", "prompt": "Empty label"},
        OPTIONS[0],
        OPTIONS[0],
        {"label": "Duplicate prompt", "prompt": OPTIONS[0]["prompt"]},
        OPTIONS[1],
    ]
    result = ChatSuggestionService.parse("```json\n" + json.dumps({"suggestions": rows}) + "\n```")
    assert [item.model_dump() for item in result] == OPTIONS
    assert ChatSuggestionService.parse('{"suggestions": null}') == []
    assert (
        ChatSuggestionService.parse(
            '{"suggestions": [{"label":"Bad\\nlabel","prompt":"Question"}]}'
        )
        == []
    )
    assert len(ChatSuggestionService.parse(json.dumps({"suggestions": [OPTIONS[0]]}))) == 1


def test_cache_is_scoped_by_user_message_and_answer(monkeypatch):
    service = ChatSuggestionService()
    model = AsyncMock(return_value=json.dumps({"suggestions": OPTIONS}))
    monkeypatch.setattr(service.model, "complete_follow_up_suggestions", model)
    for user, message, digest in [(1, 2, "a"), (2, 2, "a"), (1, 3, "a"), (1, 2, "b")]:
        asyncio.run(
            service.suggest(
                user_id=user, message_id=message, answer_hash=digest, question="Q", answer="A"
            )
        )
    assert model.await_count == 4


def test_model_request_uses_small_budget_and_same_provider_fallback(monkeypatch):
    service = ChatService()
    monkeypatch.setattr(service, "_use_nvidia_primary", lambda: True)
    primary = AsyncMock(side_effect=_NvidiaFallbackError("Retry"))
    fallback = AsyncMock(return_value=json.dumps({"suggestions": OPTIONS}))
    monkeypatch.setattr(service, "_complete_with_nvidia", primary)
    monkeypatch.setattr(service, "_complete_with_ollama", fallback)
    asyncio.run(service.complete_follow_up_suggestions("Tamil question", "The answer"))
    body = primary.call_args.args[0]
    assert body["nvidia_max_tokens"] == 512
    assert body["stream"] is False
    assert json.loads(body["messages"][1]["content"])["answer"] == "The answer"
    assert "Match the user's language" in body["messages"][0]["content"]
    fallback.assert_awaited_once_with(body)
