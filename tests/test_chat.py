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
