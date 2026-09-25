import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
import requests

from app.config import settings
from app.models.chat import ChatMessageRecord, ChatSession
from app.schemas.chat import ChatHistoryMessage
from app.services.chat_service import ChatModelUnavailableError, ChatService, _NvidiaFallbackError
from app.services.chat_vision_service import ChatVisionService
from app.services.conversation_guidance import PERSONAL_CONVERSATION_PROMPT
from app.services.image_parser_service import VisionModelUnavailableError


def _register_and_login(client, email="chatuser@example.com", password="chat12345"):
    client.post("/users/", json={"fullname": "Chat User", "email": email, "password": password})

    login_response = client.post("/users/login", json={"email": email, "password": password})

    return login_response.json()["access_token"]


def test_chat_requires_authentication(client):
    response = client.post(
        "/chat/",
        json={
            "message": "Hi",
            "history": [{"role": "user", "content": "Hi"}],
            "reference_history": [],
        },
    )

    assert response.status_code in (401, 403)


def test_chat_rejects_invalid_token(client):
    response = client.post(
        "/chat/",
        json={
            "message": "Hi",
            "history": [{"role": "user", "content": "Hi"}],
            "reference_history": [],
        },
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert response.status_code == 401


def test_chat_vision_creates_session_and_persists_both_turns(client, monkeypatch, valid_png_bytes):
    token = _register_and_login(client, "vision-upload@example.com")
    monkeypatch.setattr(
        ChatVisionService,
        "describe",
        lambda self, image_bytes, user_message: "The image shows a red bicycle.",
    )
    response = client.post(
        "/chat/vision",
        headers={"Authorization": f"Bearer {token}"},
        files={"image": ("sample.png", valid_png_bytes, "image/png")},
        data={"message": "What is in this image?"},
    )
    assert response.status_code == 200
    assert response.json()["response"] == "The image shows a red bicycle."
    session_id = response.json()["session_id"]
    history = client.get(
        f"/chat/sessions/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["messages"]
    assert [(item["sender"], item["content"]) for item in history] == [
        ("user", "What is in this image?"),
        ("bot", "The image shows a red bicycle."),
    ]
    assert history[0]["image_url"].startswith("data:image/png;base64,")
    assert history[1]["image_url"] is None


def test_chat_vision_continues_existing_session_and_persists_image(
    client, monkeypatch, valid_png_bytes
):
    token = _register_and_login(client, "vision-continue@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    existing = client.post(
        "/chat/sessions", headers=headers, json={"title": "Existing chat"}
    ).json()
    monkeypatch.setattr(
        ChatVisionService,
        "describe",
        lambda self, image_bytes, user_message: "It contains a blue logo.",
    )
    response = client.post(
        "/chat/vision",
        headers=headers,
        files={"image": ("logo.png", valid_png_bytes, "image/png")},
        data={"session_id": str(existing["id"])},
    )
    assert response.status_code == 200
    assert response.json()["session_id"] == existing["id"]
    history = client.get(f"/chat/sessions/{existing['id']}", headers=headers).json()["messages"]
    assert [(item["sender"], item["content"]) for item in history] == [
        ("user", "[Image]"),
        ("bot", "It contains a blue logo."),
    ]
    assert all("private-image-bytes" not in item["content"] for item in history)
    assert history[0]["image_url"].startswith("data:image/png;base64,")


def test_chat_vision_returns_503_when_model_is_unavailable(client, monkeypatch, valid_png_bytes):
    token = _register_and_login(client, "vision-unavailable@example.com")

    def unavailable(self, image_bytes, user_message):
        raise VisionModelUnavailableError("Chat vision model unavailable. Pull the model.")

    monkeypatch.setattr(ChatVisionService, "describe", unavailable)
    response = client.post(
        "/chat/vision",
        headers={"Authorization": f"Bearer {token}"},
        files={"image": ("sample.png", valid_png_bytes, "image/png")},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Chat vision model unavailable. Pull the model."


def test_chat_vision_rejects_oversized_image(client):
    token = _register_and_login(client, "vision-large@example.com")
    response = client.post(
        "/chat/vision",
        headers={"Authorization": f"Bearer {token}"},
        files={"image": ("large.jpg", b"x" * (8 * 1024 * 1024 + 1), "image/jpeg")},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "Image is too large. Maximum size is 8 MB."


def test_chat_vision_rejects_invalid_content_type(client):
    token = _register_and_login(client, "vision-type@example.com")
    response = client.post(
        "/chat/vision",
        headers={"Authorization": f"Bearer {token}"},
        files={"image": ("payload.txt", b"not-an-image", "text/plain")},
    )
    assert response.status_code == 415
    assert "Unsupported file type" in response.json()["detail"]


def test_chat_vision_rejects_spoofed_image_content(client, monkeypatch):
    token = _register_and_login(client, "vision-spoof@example.com")

    def must_not_run(*args, **kwargs):
        raise AssertionError("Spoofed image reached the vision model")

    monkeypatch.setattr(ChatVisionService, "describe", must_not_run)
    response = client.post(
        "/chat/vision",
        headers={"Authorization": f"Bearer {token}"},
        files={"image": ("fake.png", b"this is not a real image", "image/png")},
    )
    assert response.status_code == 422
    assert "valid supported image" in response.json()["detail"].lower()


def test_chat_vision_has_strict_rate_limit(client, monkeypatch, valid_png_bytes):
    token = _register_and_login(client, "vision-limit@example.com")
    monkeypatch.setattr(
        ChatVisionService,
        "describe",
        lambda self, image_bytes, user_message: "Description",
    )
    headers = {"Authorization": f"Bearer {token}"}

    responses = [
        client.post(
            "/chat/vision",
            headers=headers,
            files={"image": ("sample.png", valid_png_bytes, "image/png")},
        )
        for _ in range(3)
    ]

    assert [response.status_code for response in responses] == [200, 200, 429]


def test_chat_vision_requires_authentication(client):
    response = client.post(
        "/chat/vision",
        files={"image": ("sample.webp", b"image", "image/webp")},
    )
    assert response.status_code in (401, 403)


def test_consolidate_sessions_preserves_messages_from_different_dates_in_one_conversation(
    client, db_session
):
    token = _register_and_login(client, "consolidate@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_ids = []
    for index in range(3):
        session = client.post(
            "/chat/sessions",
            headers=headers,
            json={"title": f"Message {index + 1}"},
        ).json()
        session_ids.append(session["id"])
        imported = client.post(
            f"/chat/sessions/{session['id']}/import",
            headers=headers,
            json={
                "messages": [
                    {"sender": "user", "content": f"Question {index + 1}"},
                    {"sender": "bot", "content": f"Answer {index + 1}"},
                ]
            },
        )
        assert imported.status_code == 200

        day = 10 if index < 2 else 11
        timestamp = datetime(2026, 8, day, 9 + index, tzinfo=timezone.utc)
        db_session.query(ChatSession).filter(ChatSession.id == session["id"]).update(
            {
                ChatSession.created_at: timestamp,
                ChatSession.updated_at: timestamp,
            }
        )
        db_session.query(ChatMessageRecord).filter(
            ChatMessageRecord.session_id == session["id"]
        ).update({ChatMessageRecord.created_at: timestamp})
        db_session.commit()

    response = client.post("/chat/sessions/consolidate", headers=headers)
    assert response.status_code == 200
    sessions = client.get("/chat/sessions", headers=headers).json()
    assert len(sessions) == 1
    history = client.get(f"/chat/sessions/{sessions[0]['id']}", headers=headers).json()["messages"]
    assert [message["content"] for message in history] == [
        "Question 1",
        "Answer 1",
        "Question 2",
        "Answer 2",
        "Question 3",
        "Answer 3",
    ]


def test_chat_success_with_valid_token(client, monkeypatch):
    token = _register_and_login(client)

    monkeypatch.setattr(
        ChatService,
        "chat",
        lambda self, message, history, reference_history, **kwargs: "Vanakkam! Nalla irukeenga?",
    )

    response = client.post(
        "/chat/",
        json={
            "message": "Hi",
            "history": [{"role": "user", "content": "Hi"}],
            "reference_history": [],
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["response"] == "Vanakkam! Nalla irukeenga?"
    assert isinstance(response.json()["session_id"], int)


def test_chat_uses_saved_session_history_instead_of_browser_history(client, monkeypatch):
    token = _register_and_login(client, email="saved-history@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    captured_histories = []

    def fake_chat(self, message, history, reference_history, **kwargs):
        captured_histories.append([(item.role, item.content) for item in history])
        return "Understood, boss." if len(captured_histories) == 1 else "Here you go, boss."

    monkeypatch.setattr(ChatService, "chat", fake_chat)
    first = client.post(
        "/chat/",
        json={"message": "Call me boss", "history": [], "reference_history": []},
        headers=headers,
    )
    session_id = first.json()["session_id"]

    second = client.post(
        f"/chat/?session_id={session_id}",
        json={"message": "Suggest a meal", "history": [], "reference_history": []},
        headers=headers,
    )

    assert second.status_code == 200
    assert captured_histories[1] == [
        ("user", "Call me boss"),
        ("assistant", "Understood, boss."),
    ]


def test_chat_timeout_returns_clean_503(client, monkeypatch):
    token = _register_and_login(client, email="text-timeout@example.com")

    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == (
            settings.OLLAMA_CONNECT_TIMEOUT_SECONDS,
            settings.OLLAMA_TIMEOUT_SECONDS,
        )
        assert kwargs["json"]["keep_alive"] == settings.OLLAMA_KEEP_ALIVE
        raise requests.ReadTimeout("cold model load exceeded the deadline")

    monkeypatch.setattr("app.services.chat_service.requests.post", timeout)
    response = client.post(
        "/chat/",
        json={"message": "Explain this", "history": [], "reference_history": []},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 503
    assert "still loading or unavailable" in response.json()["detail"]
    sessions = client.get(
        "/chat/sessions",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    detail = client.get(
        f"/chat/sessions/{sessions[0]['id']}",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert detail["messages"] == []


def test_chat_stream_timeout_returns_error_chunk_and_closes_cleanly(client, monkeypatch):
    token = _register_and_login(client, email="stream-timeout@example.com")
    closed = False

    class TimeoutStream:
        async def __aenter__(self):
            raise httpx.ReadTimeout(
                "cold model load exceeded the deadline",
                request=httpx.Request("POST", settings.OLLAMA_URL),
            )

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            assert timeout.read == settings.OLLAMA_TIMEOUT_SECONDS
            assert timeout.connect == settings.OLLAMA_CONNECT_TIMEOUT_SECONDS

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            nonlocal closed
            closed = True

        def stream(self, method, url, *, json):
            assert method == "POST"
            assert json["keep_alive"] == settings.OLLAMA_KEEP_ALIVE
            return TimeoutStream()

    monkeypatch.setattr("app.services.chat_service.httpx.AsyncClient", FakeAsyncClient)
    response = client.post(
        "/chat/stream",
        json={"message": "Explain this", "history": [], "reference_history": []},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    events = [__import__("json").loads(line) for line in response.text.splitlines()]
    assert events[0]["type"] == "session"
    assert events[-1]["type"] == "error"
    assert "still loading or unavailable" in events[-1]["message"]
    assert closed is True
    session_response = client.get(
        f"/chat/sessions/{events[0]['session_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert [message["content"] for message in session_response.json()["messages"]] == [
        "Explain this",
        f"Response interrupted: {events[-1]['message']}",
    ]


def test_chat_stream_returns_session_tokens_and_persists_answer(client, monkeypatch):
    token = _register_and_login(client, email="stream@example.com")

    async def fake_stream(self, message, history, reference_history, **kwargs):
        yield "Hello "
        yield "there"

    monkeypatch.setattr(ChatService, "stream_chat", fake_stream)
    response = client.post(
        "/chat/stream",
        json={"message": "Hi", "history": [], "reference_history": []},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    events = [__import__("json").loads(line) for line in response.text.splitlines()]
    assert events[0]["type"] == "session"
    assert [event.get("content") for event in events if event["type"] == "token"] == [
        "Hello ",
        "there",
    ]
    assert events[-1] == {"type": "done"}

    session_response = client.get(
        f"/chat/sessions/{events[0]['session_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert session_response.json()["messages"][-1]["content"] == "Hello there"


def test_chat_stream_reuses_latest_persisted_image_for_follow_up(
    client,
    db_session,
    monkeypatch,
):
    token = _register_and_login(client, email="image-follow-up@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post(
        "/chat/sessions",
        json={"title": "Image chat"},
        headers=headers,
    ).json()["id"]
    db_session.add(
        ChatMessageRecord(
            session_id=session_id,
            sender="user",
            content="What is shown?",
            image_data=b"persisted-image",
            image_content_type="image/png",
        )
    )
    db_session.add(
        ChatMessageRecord(
            session_id=session_id,
            sender="bot",
            content="The image shows several foods.",
        )
    )
    db_session.commit()
    captured = {}

    def describe(self, image_bytes, user_message, conversation_history=()):
        captured["image"] = image_bytes
        captured["message"] = user_message
        captured["history"] = [item.content for item in conversation_history]
        return "The Indian food shown is dosa."

    async def unexpected_text_stream(*args, **kwargs):
        raise AssertionError("An image follow-up must use the vision service")
        yield ""

    monkeypatch.setattr(ChatVisionService, "describe", describe)
    monkeypatch.setattr(ChatService, "stream_chat", unexpected_text_stream)

    response = client.post(
        f"/chat/stream?session_id={session_id}",
        json={
            "message": "Can you identify the Indian food?",
            "history": [],
            "reference_history": [],
        },
        headers=headers,
    )

    events = [__import__("json").loads(line) for line in response.text.splitlines()]
    assert [event.get("content") for event in events if event["type"] == "token"] == [
        "The Indian food shown is dosa."
    ]
    assert captured == {
        "image": b"persisted-image",
        "message": "Can you identify the Indian food?",
        "history": ["What is shown?", "The image shows several foods."],
    }
    persisted = client.get(f"/chat/sessions/{session_id}", headers=headers).json()["messages"]
    assert persisted[-2]["content"] == "Can you identify the Indian food?"
    assert persisted[-1]["content"] == "The Indian food shown is dosa."


def test_chat_stream_selects_relevant_older_image_in_multi_image_history(
    client,
    db_session,
    monkeypatch,
):
    token = _register_and_login(client, email="multi-image-follow-up@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post(
        "/chat/sessions", json={"title": "Multiple images"}, headers=headers
    ).json()["id"]
    db_session.add_all(
        [
            ChatMessageRecord(
                session_id=session_id,
                sender="user",
                content="How many food items are in this image?",
                image_data=b"food-image",
                image_content_type="image/png",
            ),
            ChatMessageRecord(
                session_id=session_id,
                sender="bot",
                content="The image contains sixteen international food items.",
            ),
            ChatMessageRecord(
                session_id=session_id,
                sender="user",
                content="How many people are in this image?",
                image_data=b"people-image",
                image_content_type="image/png",
            ),
            ChatMessageRecord(
                session_id=session_id,
                sender="bot",
                content="The image shows a group of eighteen people.",
            ),
        ]
    )
    db_session.commit()
    selected_images = []

    def describe(self, image_bytes, user_message, conversation_history=()):
        selected_images.append(image_bytes)
        return "The selected historical image was analyzed."

    monkeypatch.setattr(ChatVisionService, "describe", describe)
    response = client.post(
        f"/chat/stream?session_id={session_id}",
        json={"message": "Can you identify the UAE food?"},
        headers=headers,
    )

    assert response.status_code == 200
    assert selected_images == [b"food-image"]


def test_chat_stream_honors_explicit_historical_image_number(
    client,
    db_session,
    monkeypatch,
):
    token = _register_and_login(client, email="numbered-image-follow-up@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post(
        "/chat/sessions", json={"title": "Numbered images"}, headers=headers
    ).json()["id"]
    for index in range(3):
        db_session.add(
            ChatMessageRecord(
                session_id=session_id,
                sender="user",
                content=f"Uploaded picture {index + 1}",
                image_data=f"image-{index + 1}".encode(),
                image_content_type="image/png",
            )
        )
        db_session.add(
            ChatMessageRecord(
                session_id=session_id,
                sender="bot",
                content=f"Description {index + 1}",
            )
        )
    db_session.commit()
    selected_images = []

    def describe(self, image_bytes, user_message, conversation_history=()):
        selected_images.append(image_bytes)
        return "Done"

    monkeypatch.setattr(ChatVisionService, "describe", describe)
    response = client.post(
        f"/chat/stream?session_id={session_id}",
        json={"message": "Tell me more about the second image"},
        headers=headers,
    )

    assert response.status_code == 200
    assert selected_images == [b"image-2"]


def test_chat_stream_treats_unrelated_topic_after_image_as_plain_text(
    client,
    db_session,
    monkeypatch,
):
    token = _register_and_login(client, email="unrelated-topic-after-image@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = client.post(
        "/chat/sessions", json={"title": "Diagram chat"}, headers=headers
    ).json()["id"]
    db_session.add(
        ChatMessageRecord(
            session_id=session_id,
            sender="user",
            content="What does this diagram show?",
            image_data=b"testing-types-diagram",
            image_content_type="image/png",
        )
    )
    db_session.add(
        ChatMessageRecord(
            session_id=session_id,
            sender="bot",
            content=(
                "The image is a diagram titled 'Software Testing Types' with categories "
                "like Functional Testing and Non-Functional Testing."
            ),
        )
    )
    db_session.commit()

    def unexpected_vision(self, image_bytes, user_message, conversation_history=()):
        raise AssertionError("An unrelated new topic must not reuse the earlier image")

    async def fake_text_stream(self, message, history, reference_history, **kwargs):
        yield "Ramen noodles are a Japanese noodle dish served in broth."

    monkeypatch.setattr(ChatVisionService, "describe", unexpected_vision)
    monkeypatch.setattr(ChatService, "stream_chat", fake_text_stream)

    response = client.post(
        f"/chat/stream?session_id={session_id}",
        json={"message": "ramen noodles", "history": [], "reference_history": []},
        headers=headers,
    )

    events = [__import__("json").loads(line) for line in response.text.splitlines()]
    assert [event.get("content") for event in events if event["type"] == "token"] == [
        "Ramen noodles are a Japanese noodle dish served in broth."
    ]


def test_chat_stream_generation_is_not_stopped_by_request_disconnect_check(client, monkeypatch):
    from starlette.requests import Request

    token = _register_and_login(client, email="disconnect@example.com")
    pulled = False

    async def fake_stream(self, message, history, reference_history, **kwargs):
        nonlocal pulled
        pulled = True
        yield "must not be read"

    async def disconnected(self):
        return True

    monkeypatch.setattr(ChatService, "stream_chat", fake_stream)
    monkeypatch.setattr(Request, "is_disconnected", disconnected)
    response = client.post(
        "/chat/stream",
        json={"message": "Stop", "history": [], "reference_history": []},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert pulled is True
    assert '"type": "token"' in response.text


def test_import_chat_session_messages(client):
    token = _register_and_login(client, email="migration@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    create_response = client.post(
        "/chat/sessions",
        json={"title": "Imported chat"},
        headers=headers,
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["id"]

    import_response = client.post(
        f"/chat/sessions/{session_id}/import",
        json={
            "messages": [
                {"sender": "user", "content": "Old question"},
                {"sender": "bot", "content": "Old answer"},
            ]
        },
        headers=headers,
    )

    assert import_response.status_code == 200
    assert import_response.json()["id"] == session_id
    assert [
        {"sender": message["sender"], "content": message["content"]}
        for message in import_response.json()["messages"]
    ] == [
        {"sender": "user", "content": "Old question"},
        {"sender": "bot", "content": "Old answer"},
    ]


def test_import_chat_session_rejects_another_users_session(client):
    first_token = _register_and_login(client, email="first-migration@example.com")
    second_token = _register_and_login(client, email="second-migration@example.com")
    create_response = client.post(
        "/chat/sessions",
        json={"title": "Private chat"},
        headers={"Authorization": f"Bearer {first_token}"},
    )
    session_id = create_response.json()["id"]

    response = client.post(
        f"/chat/sessions/{session_id}/import",
        json={"messages": [{"sender": "user", "content": "Private"}]},
        headers={"Authorization": f"Bearer {second_token}"},
    )

    assert response.status_code == 404


def test_chat_service_uses_request_message_when_history_is_stale(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "Sure, I understand."}}

    def fake_post(url, json, timeout):
        captured["body"] = json
        return FakeResponse()

    monkeypatch.setattr("app.services.chat_service.requests.post", fake_post)

    answer = ChatService().chat("enakku menu kaatu", [], [])

    assert answer == "Sure, I understand."
    assert captured["body"]["messages"][-1] == {
        "role": "user",
        "content": "enakku menu kaatu",
    }
    assert captured["body"]["keep_alive"] == settings.OLLAMA_KEEP_ALIVE
    assert captured["body"]["think"] is settings.OLLAMA_CHAT_THINK
    assert captured["body"]["options"]["num_predict"] == settings.OLLAMA_CHAT_MAX_TOKENS


def test_chat_service_does_not_duplicate_latest_message(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "Sure"}}

    def fake_post(url, json, timeout):
        captured["body"] = json
        return FakeResponse()

    monkeypatch.setattr("app.services.chat_service.requests.post", fake_post)

    from app.schemas.chat import ChatHistoryMessage

    ChatService().chat(
        "sapadu list sollu",
        [ChatHistoryMessage(role="user", content="sapadu list sollu")],
        [],
    )

    matches = [
        item
        for item in captured["body"]["messages"]
        if item["role"] == "user" and item["content"] == "sapadu list sollu"
    ]
    assert len(matches) == 1


def test_chat_service_bounds_old_context_for_faster_local_inference():
    history = [
        ChatHistoryMessage(role="user", content=f"history-{index}")
        for index in range(ChatService.HISTORY_MESSAGE_LIMIT + 3)
    ]
    long_reference = "x" * (ChatService.CONTEXT_MESSAGE_CHAR_LIMIT + 500)
    reference_history = [
        ChatHistoryMessage(role="user", content=f"reference-{index}")
        for index in range(ChatService.REFERENCE_MESSAGE_LIMIT + 2)
    ]
    reference_history[-1] = ChatHistoryMessage(role="user", content=long_reference)

    _, body = ChatService()._build_request_body(
        "latest question",
        history,
        reference_history,
        stream=True,
    )
    contents = [item["content"] for item in body["messages"]]

    assert "history-0" not in contents
    assert "history-3" in contents
    assert "reference-0" not in contents
    assert "reference-2" in contents
    truncated = next(content for content in contents if "[truncated]" in content)
    assert len(truncated) <= ChatService.CONTEXT_MESSAGE_CHAR_LIMIT


def test_build_request_body_injects_topic_anchor_for_a_real_title():
    _, body = ChatService()._build_request_body(
        "What else should I add?",
        [],
        [],
        stream=False,
        session_title="Meal plan for diabetic patients",
    )

    topic_messages = [
        item
        for item in body["messages"]
        if item["role"] == "system" and "CURRENT CONVERSATION TOPIC" in item["content"]
    ]
    assert len(topic_messages) == 1
    assert "Meal plan for diabetic patients" in topic_messages[0]["content"]


@pytest.mark.parametrize("session_title", ["New chat", None])
def test_build_request_body_omits_topic_anchor_for_untitled_sessions(session_title):
    _, body = ChatService()._build_request_body(
        "What else should I add?",
        [],
        [],
        stream=False,
        session_title=session_title,
    )

    assert not any("CURRENT CONVERSATION TOPIC" in item["content"] for item in body["messages"])


def test_chat_thread_session_title_into_request_body(monkeypatch):
    captured = {}
    original = ChatService._build_request_body

    def spy(self, message, history, reference_history, *, stream, **kwargs):
        captured.update(kwargs)
        return original(self, message, history, reference_history, stream=stream, **kwargs)

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "Sure, here's more."}}

    monkeypatch.setattr(ChatService, "_build_request_body", spy)
    monkeypatch.setattr(
        "app.services.chat_service.requests.post",
        lambda url, json, timeout: FakeResponse(),
    )

    ChatService().chat("Hello", [], [], session_title="Meal plan for diabetic patients")

    assert captured["session_title"] == "Meal plan for diabetic patients"


def test_delete_user_turn_removes_its_following_bot_response(client):
    token = _register_and_login(client, "delete-turn@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session = client.post("/chat/sessions", headers=headers, json={"title": "Delete turn"}).json()
    imported = client.post(
        f"/chat/sessions/{session['id']}/import",
        headers=headers,
        json={
            "messages": [
                {"sender": "user", "content": "Remove this"},
                {"sender": "bot", "content": "Remove this response"},
                {"sender": "user", "content": "Keep this"},
            ]
        },
    ).json()

    response = client.delete(
        f"/chat/sessions/{session['id']}/messages/{imported['messages'][0]['id']}",
        headers=headers,
    )

    assert response.status_code == 200
    history = client.get(f"/chat/sessions/{session['id']}", headers=headers).json()["messages"]
    assert [message["content"] for message in history] == ["Keep this"]


def test_chat_service_preserves_old_standing_preference_outside_recent_history():
    history = [ChatHistoryMessage(role="user", content="Please call me Master in every chat.")]
    history.extend(
        ChatHistoryMessage(role="user", content=f"ordinary message {index}")
        for index in range(ChatService.HISTORY_MESSAGE_LIMIT + 2)
    )

    _, body = ChatService()._build_request_body(
        "Give me some motivation",
        history,
        [],
        stream=True,
    )

    assert any(
        item["role"] == "user" and item["content"] == "Please call me Master in every chat."
        for item in body["messages"]
    )


def test_system_prompt_requests_readable_markdown_and_relevant_emojis():
    prompt = ChatService.SYSTEM_PROMPT

    assert "**bold text**" in prompt
    assert "one or two relevant emojis" in prompt
    assert "code, JSON, or another strict format" in prompt
    assert "one short, relevant next-step suggestion" in prompt
    assert "Do not use the same generic suggestion every time" in prompt
    assert "Omit this closing" in prompt and "suggestion when" in prompt


def test_system_prompt_no_longer_deprioritizes_conversation_history():
    prompt = ChatService.SYSTEM_PROMPT

    assert "use conversation history only as context" not in prompt
    assert "stated conversation topic" in prompt
    assert "clearly changes the subject" in prompt


@pytest.mark.parametrize("stream", [False, True])
def test_personal_reply_retains_context_without_report_instructions(stream):
    history = [
        ChatHistoryMessage(
            role="user", content="My friend hasn't replied. I keep checking my phone."
        ),
        ChatHistoryMessage(role="user", content="Please reply in Tanglish."),
    ]
    language, body = ChatService()._build_request_body("What can I do?", history, [], stream=stream)
    assert language == "Tanglish (Tamil written in Latin letters)"
    assert body["messages"][0]["content"] == PERSONAL_CONVERSATION_PROMPT
    assert any(item["content"] == history[0].content for item in body["messages"])
    assert body["messages"][-1] == {"role": "user", "content": "What can I do?"}


@pytest.mark.parametrize("source", ["web", "document"])
def test_personal_wording_does_not_replace_grounded_answer_instructions(source):
    reference = ChatHistoryMessage(
        role="user", content=ChatService.DOCUMENT_CONTEXT_PREFIX + "Uploaded report contents"
    )
    _, body = ChatService()._build_request_body(
        "I'm feeling low. Explain the support options in this report.",
        [],
        [reference] if source == "document" else [],
        stream=True,
        web_search_context="Verified support options" if source == "web" else None,
    )
    assert body["messages"][0]["content"] == ChatService.SYSTEM_PROMPT
    expected_source = reference.content if source == "document" else "Verified support options"
    assert any(expected_source in item["content"] for item in body["messages"])


def test_language_detection_uses_latest_message_only():
    assert ChatService.detect_language("Explain South Indian food") == "English"
    assert ChatService.detect_language("எனக்கு உணவு பற்றி சொல்லுங்கள்") == "Tamil (Tamil script)"
    assert ChatService.detect_language("मुझे भारतीय खाना बताओ") == "Hindi (Devanagari script)"
    assert ChatService.detect_language("enakku nalla sapadu sollu") == (
        "Tanglish (Tamil written in Latin letters)"
    )
    assert ChatService.detect_language("mujhe accha khana batao") == (
        "Hinglish (Hindi written in Latin letters)"
    )


def test_language_detection_accepts_common_tanglish_spelling_variants():
    assert ChatService.detect_language("vannakam mapla") == (
        "Tanglish (Tamil written in Latin letters)"
    )


def test_chat_defaults_to_english_for_ambiguous_greeting(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "Hello! How can I help?"}}

    def fake_post(url, json, timeout):
        captured["body"] = json
        return FakeResponse()

    monkeypatch.setattr("app.services.chat_service.requests.post", fake_post)

    ChatService().chat("hiii", [], [])

    assert "respond only in English" in captured["body"]["messages"][-2]["content"]


def test_chat_does_not_auto_switch_for_tanglish_content(monkeypatch):
    _, body = ChatService()._build_request_body(
        "enakku healthy food sollu",
        [],
        [],
        stream=False,
    )

    assert "respond only in English" in body["messages"][-2]["content"]


def test_explicit_language_choice_persists_until_user_changes_it():
    from app.schemas.chat import ChatHistoryMessage

    history = [
        ChatHistoryMessage(role="user", content="Please reply in Tanglish"),
        ChatHistoryMessage(role="assistant", content="Sari, kandippa."),
    ]
    language, _ = ChatService()._build_request_body(
        "Suggest a healthy breakfast",
        history,
        [],
        stream=False,
    )
    assert language == "Tanglish (Tamil written in Latin letters)"

    history.append(ChatHistoryMessage(role="user", content="Only speak English now"))
    language, _ = ChatService()._build_request_body(
        "What about lunch?",
        history,
        [],
        stream=False,
    )
    assert language == "English"
    assert ChatService.detect_language("can you explain this in tanglish") == (
        "Tanglish (Tamil written in Latin letters)"
    )


def test_content_offer_gets_direct_natural_tanglish_reply(monkeypatch):
    def unexpected_post(*args, **kwargs):
        raise AssertionError("The simple content-offer intent should not call Ollama")

    monkeypatch.setattr("app.services.chat_service.requests.post", unexpected_post)

    answer = ChatService().chat(
        "can I send some content, can you explain it in thanglish?",
        [],
        [],
    )

    assert answer == (
        "Kandippa, content-a anuppunga. Adha simple-ah puriyura maadhiri "
        "Thanglish-la explain panren."
    )


def test_latest_language_instruction_follows_old_tanglish_history(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "Here is the answer."}}

    def fake_post(url, json, timeout):
        captured["body"] = json
        return FakeResponse()

    monkeypatch.setattr("app.services.chat_service.requests.post", fake_post)

    from app.schemas.chat import ChatHistoryMessage

    ChatService().chat(
        "Which character has the best development?",
        [
            ChatHistoryMessage(role="user", content="enakku anime pathi sollu"),
            ChatHistoryMessage(role="assistant", content="Sari, anime pathi solren"),
        ],
        [],
    )

    messages = captured["body"]["messages"]
    assert messages[-2]["role"] == "system"
    assert "respond only in English" in messages[-2]["content"]
    assert messages[-1] == {
        "role": "user",
        "content": "Which character has the best development?",
    }
    assert not any(item.get("content") == "Sari, anime pathi solren" for item in messages)


def test_english_response_written_in_tanglish_is_rewritten(monkeypatch):
    captured_bodies = []

    class FakeResponse:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": self.content}}

    responses = iter(
        [
            FakeResponse("Aama, kandippa! Enna help venum nu sollunga."),
            FakeResponse("Certainly! How can I help you?"),
        ]
    )

    def fake_post(url, json, timeout):
        captured_bodies.append(json.copy())
        return next(responses)

    monkeypatch.setattr("app.services.chat_service.requests.post", fake_post)

    answer = ChatService().chat("How can you help me?", [], [])

    assert answer == "Certainly! How can I help you?"
    assert len(captured_bodies) == 2
    assert "only in English" in captured_bodies[1]["messages"][-1]["content"]
    assert "transliterated non-English words" in (captured_bodies[1]["messages"][-1]["content"])


def test_tanglish_response_in_tamil_script_is_rewritten(monkeypatch):
    captured_bodies = []

    class FakeResponse:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": self.content}}

    responses = iter(
        [
            FakeResponse("உங்களுக்கு என்ன உணவு பிடிக்கும்?"),
            FakeResponse("Ungalukku enna unavu pidikkum?"),
        ]
    )

    def fake_post(url, json, timeout):
        captured_bodies.append(json.copy())
        return next(responses)

    monkeypatch.setattr("app.services.chat_service.requests.post", fake_post)

    answer = ChatService().chat("thanglish la oru kelvi kelu", [], [])

    assert answer == "Ungalukku enna unavu pidikkum?"
    assert len(captured_bodies) == 2
    assert (
        "Use Latin/English letters for every word"
        in (captured_bodies[1]["messages"][-1]["content"])
    )


def test_system_prompt_separates_source_content_from_format_example():
    prompt = ChatService.SYSTEM_PROMPT

    assert "earlier user content as the source" in prompt
    assert "later example only as the desired" in prompt
    assert "example's subject" in prompt
    assert "**Repro Steps:**" in prompt
    assert "**Expected Result:**" in prompt
    assert 'do not write "Not specified"' in prompt
    assert 'only for a "bug type sentence"' in prompt


def test_system_prompt_requires_natural_tanglish_without_inventing_content():
    prompt = ChatService.TANGLISH_STYLE_PROMPT

    assert "natural conversational Tamil" in prompt
    assert "Never" in prompt and "word-by-word" in prompt
    assert "content-a anuppunga" in prompt
    assert "Do not invent the content the user intends to send" in ChatService.SYSTEM_PROMPT


def test_english_request_does_not_receive_tanglish_examples():
    _, body = ChatService()._build_request_body("Give me some motivation", [], [], stream=True)

    prompt_text = "\n".join(item["content"] for item in body["messages"])
    assert "respond only in English" in prompt_text
    assert "content-a anuppunga" not in prompt_text
    assert "Aama, kandippa" not in prompt_text


def test_explicit_tanglish_request_receives_tanglish_style_prompt():
    _, body = ChatService()._build_request_body("Please reply in Tanglish", [], [], stream=True)

    assert any(
        item["role"] == "system" and "content-a anuppunga" in item["content"]
        for item in body["messages"]
    )


def test_language_choice_is_not_a_cross_chat_standing_preference():
    assert ChatService.is_standing_preference("Please call me Master in every chat")
    assert not ChatService.is_standing_preference("I prefer Tanglish")


def test_latest_instruction_forbids_metadata_from_old_examples(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "**Bug Type:** Functional Bug"}}

    def fake_post(url, json, timeout):
        captured["body"] = json
        return FakeResponse()

    monkeypatch.setattr("app.services.chat_service.requests.post", fake_post)

    ChatService().chat(
        "User can be created, but the email is not triggered. Give the bug type sentence.",
        [],
        [],
    )

    instruction = captured["body"]["messages"][-2]["content"]
    assert "Use no timestamp, category, priority, issue number" in instruction
    assert "Never copy metadata or facts from an example" in instruction


def test_nvidia_chat_success_does_not_call_ollama(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr(settings, "NVIDIA_CHAT_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "NVIDIA_CHAT_COMPLETE_TIMEOUT_SECONDS", 120)
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "NVIDIA answer"}}]}

    def post(url, **kwargs):
        assert kwargs["timeout"] == (settings.NVIDIA_CHAT_CONNECT_TIMEOUT_SECONDS, 120)
        calls.append(url)
        return Response()

    monkeypatch.setattr("app.services.chat_service.requests.post", post)

    assert ChatService().chat("Hello", [], []) == "NVIDIA answer"
    assert calls == [f"{settings.NVIDIA_API_BASE_URL}/chat/completions"]


def test_nvidia_chat_failure_calls_ollama_once_with_same_messages(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-key")
    calls = []

    class Response:
        def __init__(self, nvidia):
            self.status_code = 503 if nvidia else 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "Ollama answer"}}

    def post(url, **kwargs):
        calls.append((url, kwargs["json"]["messages"]))
        return Response("nvidia.com" in url)

    monkeypatch.setattr("app.services.chat_service.requests.post", post)

    assert ChatService().chat("Hello", [], []) == "Ollama answer"
    assert len(calls) == 4
    assert all(call[1] == calls[0][1] for call in calls)


def test_malformed_nvidia_chat_200_response_calls_ollama_once(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-key")
    calls = []

    class NvidiaResponse:
        status_code = 200

        def json(self):
            return {"choices": []}

    class OllamaResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "Fallback answer"}}

    def post(url, **kwargs):
        calls.append(url)
        return NvidiaResponse() if "nvidia.com" in url else OllamaResponse()

    monkeypatch.setattr("app.services.chat_service.requests.post", post)

    assert ChatService().chat("Hello", [], []) == "Fallback answer"
    assert len(calls) == 2


def test_nvidia_language_correction_stays_on_nvidia(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-key")
    calls = []
    answers = iter(["Aama, enna help venum?", "How can I help you?"])

    class Response:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": next(answers)}}]}

    def post(url, **kwargs):
        calls.append(url)
        return Response()

    monkeypatch.setattr("app.services.chat_service.requests.post", post)

    assert ChatService().chat("Hello", [], []) == "How can I help you?"
    assert calls == [
        f"{settings.NVIDIA_API_BASE_URL}/chat/completions",
        f"{settings.NVIDIA_API_BASE_URL}/chat/completions",
    ]


def test_stream_falls_back_before_nvidia_content_is_exposed(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    calls = {"nvidia": 0, "ollama": 0}

    async def nvidia(_body):
        calls["nvidia"] += 1
        if False:
            yield ""
        raise _NvidiaFallbackError("unavailable")

    async def ollama(_body):
        calls["ollama"] += 1
        yield "fallback"

    service = ChatService()
    monkeypatch.setattr(service, "_stream_nvidia", nvidia)
    monkeypatch.setattr(service, "_stream_ollama", ollama)

    async def collect():
        return [chunk async for chunk in service.stream_chat("Hello", [], [])]

    assert asyncio.run(collect()) == ["fallback"]
    assert calls == {"nvidia": 1, "ollama": 1}


def test_stream_does_not_fallback_after_nvidia_content_is_exposed(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    ollama_calls = 0

    async def nvidia(_body):
        yield "partial"
        raise _NvidiaFallbackError("interrupted")

    async def ollama(_body):
        nonlocal ollama_calls
        ollama_calls += 1
        yield "fallback"

    service = ChatService()
    monkeypatch.setattr(service, "_stream_nvidia", nvidia)
    monkeypatch.setattr(service, "_stream_ollama", ollama)

    async def collect():
        return [chunk async for chunk in service.stream_chat("Hello", [], [])]

    with pytest.raises(ChatModelUnavailableError, match="interrupted"):
        asyncio.run(collect())
    assert ollama_calls == 0


def test_nvidia_sse_error_event_falls_back_before_content_is_exposed(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-key")
    original_client = httpx.AsyncClient

    async def handler(request):
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text='data: {"error":{"message":"provider unavailable"}}\n\ndata: [DONE]\n\n',
        )

    def mocked_client(*args, **kwargs):
        return original_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    async def ollama(_body):
        yield "fallback"

    monkeypatch.setattr("app.services.chat_service.httpx.AsyncClient", mocked_client)
    service = ChatService()
    monkeypatch.setattr(service, "_stream_ollama", ollama)

    async def collect():
        return [chunk async for chunk in service.stream_chat("Hello", [], [])]

    assert asyncio.run(collect()) == ["fallback"]


def test_complete_chat_uses_nvidia_non_streaming_response(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr(settings, "NVIDIA_CHAT_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "DOCUMENT_AI_TIMEOUT_SECONDS", 90)
    original_client = httpx.AsyncClient
    seen_body = {}

    async def handler(request):
        # A document can take longer than the streaming chat's idle timeout
        # before its first byte arrives; do not prematurely fall back to Ollama.
        assert request.extensions["timeout"]["read"] == 90
        seen_body.update(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "Grounded answer"},
                    }
                ]
            },
        )

    def mocked_client(*args, **kwargs):
        return original_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.chat_service.httpx.AsyncClient", mocked_client)
    answer = asyncio.run(ChatService().complete_chat("Read this document", [], []))

    assert answer == "Grounded answer"
    assert seen_body["stream"] is False


def test_complete_chat_falls_back_once_and_uses_document_output_budget(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-key")
    captured = {}

    async def unavailable(self, body, *, allow_fallback=True):
        del self, body, allow_fallback
        raise _NvidiaFallbackError("temporary provider failure")

    async def ollama(self, body):
        del self
        captured.update(body)
        return "Fallback answer"

    monkeypatch.setattr(ChatService, "_complete_with_nvidia", unavailable)
    monkeypatch.setattr(ChatService, "_complete_with_ollama", ollama)
    answer = asyncio.run(ChatService().complete_chat("Read this document", [], []))

    assert answer == "Fallback answer"
    assert captured["options"]["num_predict"] == settings.DOCUMENT_AI_MAX_TOKENS
    assert captured["nvidia_max_tokens"] == settings.DOCUMENT_AI_MAX_TOKENS


@pytest.mark.parametrize("statuses", [[503, 200], [503, 503, 200], [503, 503, 503], [401]])
def test_document_completion_retries_transient_gateway_failure(monkeypatch, statuses):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-key")
    original_client = httpx.AsyncClient
    calls = []
    fallback = AsyncMock(return_value="Local answer")

    async def handler(request):
        code = statuses[len(calls)]
        calls.append(code)
        return httpx.Response(
            code,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": "Document answer"}}]
            },
        )

    def mocked_client(*args, **kwargs):
        return original_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.chat_service.httpx.AsyncClient", mocked_client)
    monkeypatch.setattr(ChatService, "_complete_with_ollama", fallback)
    answer = asyncio.run(ChatService().complete_chat("Read this document", [], []))
    assert calls == statuses
    assert answer == ("Document answer" if statuses[-1] == 200 else "Local answer")
    assert fallback.await_count == (0 if statuses[-1] == 200 else 1)


def test_complete_chat_returns_immediate_answer_without_calling_provider(monkeypatch):
    async def unexpected(*args, **kwargs):
        raise AssertionError("Immediate answer must not call a provider")

    monkeypatch.setattr(ChatService, "_complete_with_ollama", unexpected)
    answer = asyncio.run(
        ChatService().complete_chat("content anuppalama, Thanglish la explain pannuva?", [], [])
    )

    assert "content-a anuppunga" in answer


@pytest.mark.parametrize(
    "question",
    [
        "can you create which type of documet",
        "why you shouldn't generate or create actual file",
        "What types of document files can you generate?",
    ],
)
def test_file_capability_answers_do_not_depend_on_the_provider(monkeypatch, question):
    async def unexpected(*args, **kwargs):
        raise AssertionError("App capability information is already known")

    monkeypatch.setattr(ChatService, "_complete_with_ollama", unexpected)
    answer = asyncio.run(ChatService().complete_chat(question, [], []))
    assert "downloadable Word (.docx)" in answer
    assert "created automatically" in answer
    assert "Build my document file" not in answer
    assert ChatService()._immediate_answer("Create a Word document from this PDF") is None


def test_complete_chat_corrects_wrong_language_with_same_provider(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    answers = ["வணக்கம்", "Hello"]
    bodies = []

    async def ollama(self, body):
        del self
        bodies.append(body)
        return answers.pop(0)

    monkeypatch.setattr(ChatService, "_complete_with_ollama", ollama)
    answer = asyncio.run(ChatService().complete_chat("Explain photosynthesis", [], []))

    assert answer == "Hello"
    assert len(bodies) == 2
    assert bodies[-1]["messages"][-1]["role"] == "system"
    assert "only in English" in bodies[-1]["messages"][-1]["content"]


def test_interrupted_answer_is_saved_with_notice_and_never_emits_done(client, monkeypatch):
    token = _register_and_login(client, email="incomplete-answer@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    failure = "The response reached its output limit before it finished. Please retry with a shorter request."

    async def truncated_stream(self, message, history, reference_history, **kwargs):
        yield "A partial answer"
        raise ChatModelUnavailableError(failure)

    monkeypatch.setattr(ChatService, "stream_chat", truncated_stream)
    response = client.post("/chat/stream", json={"message": "Explain this"}, headers=headers)
    events = [__import__("json").loads(line) for line in response.text.splitlines()]
    assert events[-1] == {"type": "error", "message": failure}
    assert not any(event["type"] == "done" for event in events)

    detail = client.get(f"/chat/sessions/{events[0]['session_id']}", headers=headers).json()
    assert [item["content"] for item in detail["messages"]] == [
        "Explain this",
        f"A partial answer\n\n> Response interrupted: {failure}",
    ]
