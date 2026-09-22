import asyncio
from unittest.mock import AsyncMock

from app.config import settings
from app.models.chat import ChatMessageRecord, ChatSession
from app.repositories.chat_repository import ChatRepository
from app.schemas.chat import ChatHistoryMessage
from app.services import chat_memory_service
from app.services.chat_service import ChatModelUnavailableError, ChatService


def _register_and_login(client, email):
    client.post("/users/", json={"fullname": "Chat User", "email": email, "password": "chat12345"})
    login_response = client.post("/users/login", json={"email": email, "password": "chat12345"})
    return login_response.json()["access_token"]


def test_get_messages_since_returns_only_the_slice_between_watermark_and_window(db_session):
    repository = ChatRepository()
    session = repository.create_session(db_session, user_id=1, title="Test")
    all_messages: list[ChatMessageRecord] = []
    for index in range(6):
        user_message, bot_message = repository.add_turn(
            db_session, session.id, f"user-{index}", f"bot-{index}"
        )
        all_messages.extend([user_message, bot_message])

    expiring = repository.get_messages_since(
        db_session,
        session.id,
        after_message_id=all_messages[2].id,
        before_message_id=all_messages[8].id,
    )

    assert [item.id for item in expiring] == [item.id for item in all_messages[3:8]]


def test_get_messages_since_with_no_watermark_starts_from_the_beginning(db_session):
    repository = ChatRepository()
    session = repository.create_session(db_session, user_id=1, title="Test")
    all_messages: list[ChatMessageRecord] = []
    for index in range(4):
        user_message, bot_message = repository.add_turn(
            db_session, session.id, f"user-{index}", f"bot-{index}"
        )
        all_messages.extend([user_message, bot_message])

    expiring = repository.get_messages_since(
        db_session,
        session.id,
        after_message_id=None,
        before_message_id=all_messages[5].id,
    )

    assert [item.id for item in expiring] == [item.id for item in all_messages[:5]]


def test_get_messages_since_excludes_internal_messages(db_session):
    repository = ChatRepository()
    session = repository.create_session(db_session, user_id=1, title="Test")
    visible = ChatMessageRecord(session_id=session.id, sender="user", content="visible")
    internal = ChatMessageRecord(
        session_id=session.id, sender="bot", content="internal", is_internal=True
    )
    db_session.add_all([visible, internal])
    db_session.commit()
    db_session.refresh(visible)
    db_session.refresh(internal)

    expiring = repository.get_messages_since(
        db_session,
        session.id,
        after_message_id=None,
        before_message_id=internal.id + 1,
    )

    assert [item.id for item in expiring] == [visible.id]


def test_summarize_expiring_turns_builds_prompt_with_prior_summary_and_new_turns(monkeypatch):
    captured = {}

    async def fake_complete_chat(self, message, history, reference_history, **kwargs):
        captured["message"] = message
        captured["kwargs"] = kwargs
        return "  Updated summary.  "

    monkeypatch.setattr(ChatService, "complete_chat", fake_complete_chat)
    expiring = [
        ChatHistoryMessage(role="user", content="What's a good breakfast?"),
        ChatHistoryMessage(role="assistant", content="Try oatmeal with fruit."),
    ]

    summary = asyncio.run(
        chat_memory_service.summarize_expiring_turns(ChatService(), "Prior summary text.", expiring)
    )

    assert summary == "Updated summary."
    assert "Prior summary text." in captured["message"]
    assert "What's a good breakfast?" in captured["message"]
    assert "Try oatmeal with fruit." in captured["message"]
    assert captured["kwargs"]["temperature"] == settings.DOCUMENT_AI_TEMPERATURE
    assert captured["kwargs"]["max_tokens"] == chat_memory_service.ROLLING_SUMMARY_MAX_TOKENS


def test_refresh_rolling_summary_does_nothing_when_window_not_yet_full(db_session, monkeypatch):
    repository = ChatRepository()
    session = repository.create_session(db_session, user_id=1, title="Test")
    repository.add_turn(db_session, session.id, "hi", "hello")

    complete_chat = AsyncMock()
    monkeypatch.setattr(ChatService, "complete_chat", complete_chat)

    asyncio.run(
        chat_memory_service.refresh_rolling_summary(
            db_session, ChatService(), repository, session.id
        )
    )

    complete_chat.assert_not_called()
    db_session.refresh(session)
    assert session.rolling_summary is None


def test_refresh_rolling_summary_folds_expiring_turns_and_advances_watermark(
    db_session, monkeypatch
):
    repository = ChatRepository()
    session = repository.create_session(db_session, user_id=1, title="Test")
    all_messages: list[ChatMessageRecord] = []
    for index in range(13):
        user_message, bot_message = repository.add_turn(
            db_session, session.id, f"user-{index}", f"bot-{index}"
        )
        all_messages.extend([user_message, bot_message])
    # 26 messages total; the visible 24-message window drops only the first turn
    # (user-0, bot-0), so exactly that pair should be folded into the summary.

    captured = {}

    async def fake_complete_chat(self, message, history, reference_history, **kwargs):
        captured["message"] = message
        return "Folded summary."

    monkeypatch.setattr(ChatService, "complete_chat", fake_complete_chat)

    asyncio.run(
        chat_memory_service.refresh_rolling_summary(
            db_session, ChatService(), repository, session.id
        )
    )

    db_session.refresh(session)
    assert session.rolling_summary == "Folded summary."
    assert session.summary_covers_through_message_id == all_messages[1].id
    assert "user-0" in captured["message"]
    assert "bot-0" in captured["message"]
    assert "user-1" not in captured["message"]


def test_refresh_rolling_summary_does_not_resummarize_without_new_messages(db_session, monkeypatch):
    repository = ChatRepository()
    session = repository.create_session(db_session, user_id=1, title="Test")
    for index in range(13):
        repository.add_turn(db_session, session.id, f"user-{index}", f"bot-{index}")

    call_count = {"n": 0}

    async def fake_complete_chat(self, message, history, reference_history, **kwargs):
        call_count["n"] += 1
        return "Summary."

    monkeypatch.setattr(ChatService, "complete_chat", fake_complete_chat)
    chat_service = ChatService()

    asyncio.run(
        chat_memory_service.refresh_rolling_summary(
            db_session, chat_service, repository, session.id
        )
    )
    assert call_count["n"] == 1

    asyncio.run(
        chat_memory_service.refresh_rolling_summary(
            db_session, chat_service, repository, session.id
        )
    )
    assert call_count["n"] == 1


def test_refresh_rolling_summary_skips_update_when_model_unavailable(db_session, monkeypatch):
    repository = ChatRepository()
    session = repository.create_session(db_session, user_id=1, title="Test")
    for index in range(13):
        repository.add_turn(db_session, session.id, f"user-{index}", f"bot-{index}")

    async def failing_complete_chat(self, message, history, reference_history, **kwargs):
        raise ChatModelUnavailableError("unavailable")

    monkeypatch.setattr(ChatService, "complete_chat", failing_complete_chat)

    asyncio.run(
        chat_memory_service.refresh_rolling_summary(
            db_session, ChatService(), repository, session.id
        )
    )

    db_session.refresh(session)
    assert session.rolling_summary is None
    assert session.summary_covers_through_message_id is None


def test_fit_history_to_token_budget_keeps_newest_and_drops_oldest(monkeypatch):
    monkeypatch.setattr(settings, "CONTEXT_TOKEN_CHAR_DIVISOR", 1)
    monkeypatch.setattr(settings, "OLLAMA_CHAT_MAX_TOKENS", 0)
    monkeypatch.setattr(settings, "CONTEXT_TOKEN_BUDGET", 430)
    history = [
        ChatHistoryMessage(role="user", content=f"{index:02d}" + "x" * 48) for index in range(10)
    ]

    trimmed = ChatService._fit_history_to_token_budget(history, [{"content": "sys"}], "current")

    assert len(trimmed) == 3
    assert [item.content[:2] for item in trimmed] == ["07", "08", "09"]


def test_fit_history_to_token_budget_always_keeps_at_least_the_newest_message(monkeypatch):
    monkeypatch.setattr(settings, "CONTEXT_TOKEN_CHAR_DIVISOR", 1)
    monkeypatch.setattr(settings, "OLLAMA_CHAT_MAX_TOKENS", 100000)
    monkeypatch.setattr(settings, "CONTEXT_TOKEN_BUDGET", 0)
    history = [ChatHistoryMessage(role="user", content="x" * 500) for _ in range(5)]

    trimmed = ChatService._fit_history_to_token_budget(history, [], "current")

    assert len(trimmed) == 1
    assert trimmed[0] == history[-1]


def test_build_request_body_combines_topic_and_rolling_summary():
    _, body = ChatService()._build_request_body(
        "What else should I add?",
        [],
        [],
        stream=False,
        session_title="Meal plan for diabetic patients",
        rolling_summary="User is diabetic and prefers South Indian food.",
    )

    memory_messages = [
        item
        for item in body["messages"]
        if item["role"] == "system" and "CURRENT CONVERSATION TOPIC" in item["content"]
    ]
    assert len(memory_messages) == 1
    content = memory_messages[0]["content"]
    assert "Meal plan for diabetic patients" in content
    assert "CONVERSATION MEMORY" in content
    assert "User is diabetic and prefers South Indian food." in content


def test_build_request_body_uses_rolling_summary_alone_without_a_title():
    _, body = ChatService()._build_request_body(
        "What else should I add?",
        [],
        [],
        stream=False,
        session_title=None,
        rolling_summary="User is diabetic.",
    )

    memory_messages = [
        item
        for item in body["messages"]
        if item["role"] == "system" and "CONVERSATION MEMORY" in item["content"]
    ]
    assert len(memory_messages) == 1
    assert "CURRENT CONVERSATION TOPIC" not in memory_messages[0]["content"]
    assert "User is diabetic." in memory_messages[0]["content"]


def test_build_request_body_omits_memory_block_without_title_or_summary():
    _, body = ChatService()._build_request_body(
        "Hello",
        [],
        [],
        stream=False,
    )

    assert not any("CONVERSATION MEMORY" in item["content"] for item in body["messages"])
    assert not any("CURRENT CONVERSATION TOPIC" in item["content"] for item in body["messages"])


def test_chat_endpoint_passes_rolling_summary_to_chat_service(client, monkeypatch, db_session):
    token = _register_and_login(client, "memory-thread@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post(
        "/chat/sessions", json={"title": "Memory thread"}, headers=headers
    ).json()["id"]
    session = db_session.get(ChatSession, session_id)
    session.rolling_summary = "Earlier the user asked about diabetic meal plans."
    db_session.commit()

    captured = {}

    def fake_chat(self, message, history, reference_history, **kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(ChatService, "chat", fake_chat)
    response = client.post(
        f"/chat/?session_id={session_id}",
        json={"message": "anything", "history": [], "reference_history": []},
        headers=headers,
    )

    assert response.status_code == 200
    assert captured["rolling_summary"] == "Earlier the user asked about diabetic meal plans."
