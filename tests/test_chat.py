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
