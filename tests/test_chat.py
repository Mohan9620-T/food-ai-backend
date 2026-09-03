from datetime import datetime, timezone

import httpx
import requests

from app.config import settings
from app.models.chat import ChatMessageRecord, ChatSession
from app.services.chat_service import ChatService
from app.services.chat_vision_service import ChatVisionService
from app.services.image_parser_service import VisionModelUnavailableError


def _register_and_login(client, email="chatuser@example.com", password="chat12345"):
    client.post("/users/", json={
        "fullname": "Chat User",
        "email": email,
        "password": password
    })

    login_response = client.post("/users/login", json={
        "email": email,
        "password": password
    })

    return login_response.json()["access_token"]


def test_chat_requires_authentication(client):
    response = client.post("/chat/", json={
        "message": "Hi",
        "history": [{"role": "user", "content": "Hi"}],
        "reference_history": []
    })

    assert response.status_code in (401, 403)


def test_chat_rejects_invalid_token(client):
    response = client.post(
        "/chat/",
        json={
            "message": "Hi",
            "history": [{"role": "user", "content": "Hi"}],
            "reference_history": []
        },
        headers={"Authorization": "Bearer not-a-real-token"}
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
    history = client.get(
        f"/chat/sessions/{existing['id']}", headers=headers
    ).json()["messages"]
    assert [(item["sender"], item["content"]) for item in history] == [
        ("user", "[Image]"),
        ("bot", "It contains a blue logo."),
    ]
    assert all("private-image-bytes" not in item["content"] for item in history)
    assert history[0]["image_url"].startswith("data:image/png;base64,")


def test_chat_vision_returns_503_when_model_is_unavailable(
    client, monkeypatch, valid_png_bytes
):
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


def test_consolidate_sessions_preserves_messages_from_different_dates_in_one_conversation(client, db_session):
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
            json={"messages": [
                {"sender": "user", "content": f"Question {index + 1}"},
                {"sender": "bot", "content": f"Answer {index + 1}"},
            ]},
        )
        assert imported.status_code == 200

        day = 10 if index < 2 else 11
        timestamp = datetime(2026, 8, day, 9 + index, tzinfo=timezone.utc)
        db_session.query(ChatSession).filter(ChatSession.id == session["id"]).update({
            ChatSession.created_at: timestamp,
            ChatSession.updated_at: timestamp,
        })
        db_session.query(ChatMessageRecord).filter(
            ChatMessageRecord.session_id == session["id"]
        ).update({ChatMessageRecord.created_at: timestamp})
        db_session.commit()

    response = client.post("/chat/sessions/consolidate", headers=headers)
    assert response.status_code == 200
    sessions = client.get("/chat/sessions", headers=headers).json()
    assert len(sessions) == 1
    history = client.get(
        f"/chat/sessions/{sessions[0]['id']}", headers=headers
    ).json()["messages"]
    assert [message["content"] for message in history] == [
        "Question 1", "Answer 1", "Question 2", "Answer 2", "Question 3", "Answer 3",
    ]


def test_chat_success_with_valid_token(client, monkeypatch):
    token = _register_and_login(client)

    monkeypatch.setattr(
        ChatService,
        "chat",
        lambda self, message, history, reference_history: "Vanakkam! Nalla irukeenga?"
    )

    response = client.post(
        "/chat/",
        json={
            "message": "Hi",
            "history": [{"role": "user", "content": "Hi"}],
            "reference_history": []
        },
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json()["response"] == "Vanakkam! Nalla irukeenga?"
    assert isinstance(response.json()["session_id"], int)


def test_chat_uses_saved_session_history_instead_of_browser_history(client, monkeypatch):
    token = _register_and_login(client, email="saved-history@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    captured_histories = []

    def fake_chat(self, message, history, reference_history):
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
        assert kwargs["timeout"] == settings.OLLAMA_TIMEOUT_SECONDS
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
        "Explain this"
    ]


def test_chat_stream_returns_session_tokens_and_persists_answer(client, monkeypatch):
    token = _register_and_login(client, email="stream@example.com")

    async def fake_stream(self, message, history, reference_history):
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
        "Hello ", "there"
    ]
    assert events[-1] == {"type": "done"}

    session_response = client.get(
        f"/chat/sessions/{events[0]['session_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert session_response.json()["messages"][-1]["content"] == "Hello there"


def test_chat_stream_generation_is_not_stopped_by_request_disconnect_check(client, monkeypatch):
    from starlette.requests import Request

    token = _register_and_login(client, email="disconnect@example.com")
    pulled = False

    async def fake_stream(self, message, history, reference_history):
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
        item for item in captured["body"]["messages"]
        if item["role"] == "user" and item["content"] == "sapadu list sollu"
    ]
    assert len(matches) == 1


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
    assert not any(
        item.get("content") == "Sari, anime pathi solren" for item in messages
    )


def test_english_response_written_in_tanglish_is_rewritten(monkeypatch):
    captured_bodies = []

    class FakeResponse:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": self.content}}

    responses = iter([
        FakeResponse("Aama, kandippa! Enna help venum nu sollunga."),
        FakeResponse("Certainly! How can I help you?"),
    ])

    def fake_post(url, json, timeout):
        captured_bodies.append(json.copy())
        return next(responses)

    monkeypatch.setattr("app.services.chat_service.requests.post", fake_post)

    answer = ChatService().chat("How can you help me?", [], [])

    assert answer == "Certainly! How can I help you?"
    assert len(captured_bodies) == 2
    assert "only in English" in captured_bodies[1]["messages"][-1]["content"]
    assert "transliterated non-English words" in (
        captured_bodies[1]["messages"][-1]["content"]
    )


def test_tanglish_response_in_tamil_script_is_rewritten(monkeypatch):
    captured_bodies = []

    class FakeResponse:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": self.content}}

    responses = iter([
        FakeResponse("உங்களுக்கு என்ன உணவு பிடிக்கும்?"),
        FakeResponse("Ungalukku enna unavu pidikkum?"),
    ])

    def fake_post(url, json, timeout):
        captured_bodies.append(json.copy())
        return next(responses)

    monkeypatch.setattr("app.services.chat_service.requests.post", fake_post)

    answer = ChatService().chat("thanglish la oru kelvi kelu", [], [])

    assert answer == "Ungalukku enna unavu pidikkum?"
    assert len(captured_bodies) == 2
    assert "Use Latin/English letters for every word" in (
        captured_bodies[1]["messages"][-1]["content"]
    )


def test_system_prompt_separates_source_content_from_format_example():
    prompt = ChatService.SYSTEM_PROMPT

    assert "earlier user content as the source" in prompt
    assert "later example only as the desired" in prompt
    assert "example's subject" in prompt
    assert "**Repro Steps:**" in prompt
    assert "**Expected Result:**" in prompt
    assert "do not write \"Not specified\"" in prompt
    assert "only for a \"bug type sentence\"" in prompt


def test_system_prompt_requires_natural_tanglish_without_inventing_content():
    prompt = ChatService.SYSTEM_PROMPT

    assert "Natural Tanglish means conversational Tamil" in prompt
    assert "Never" in prompt and "word-by-word translations" in prompt
    assert "Do not invent the content the user intends to send" in prompt
    assert "content-a anuppunga" in prompt


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
