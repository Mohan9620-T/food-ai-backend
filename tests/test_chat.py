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
        lambda self, history, reference_history: "Vanakkam! Nalla irukeenga?"
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