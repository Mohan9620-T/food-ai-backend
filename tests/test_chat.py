from datetime import datetime, timezone

from app.models.chat import ChatMessageRecord, ChatSession
from app.services.chat_service import ChatService


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


def test_chat_stream_checks_disconnect_before_pulling_ollama(client, monkeypatch):
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
    assert pulled is False
    assert '"type": "token"' not in response.text


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
            return {"message": {"content": "Sari, purinjathu."}}

    def fake_post(url, json, timeout):
        captured["body"] = json
        return FakeResponse()

    monkeypatch.setattr("app.services.chat_service.requests.post", fake_post)

    answer = ChatService().chat("enakku menu kaatu", [], [])

    assert answer == "Sari, purinjathu."
    assert captured["body"]["messages"][-1] == {
        "role": "user",
        "content": "enakku menu kaatu",
    }


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
