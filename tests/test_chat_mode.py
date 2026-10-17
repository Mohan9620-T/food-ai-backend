"""Router-level checks that `mode` reaches ChatService unchanged, and that an
unknown mode is never a validation error (resolution happens inside the service)."""

import json

import pytest

from app.services.chat_service import ChatService


def _register_and_login(client, email="mode-test@example.com", password="mode-password"):
    client.post("/users/", json={"fullname": "Mode Tester", "email": email, "password": password})
    return client.post("/users/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]


@pytest.mark.parametrize("mode", ["reasoning", "math", "coding", "planning", "document", None])
def test_atomic_chat_endpoint_passes_mode_through_unchanged(client, monkeypatch, mode):
    token = _register_and_login(client, email=f"mode-atomic-{mode}@example.com")
    received = {}

    def fake_chat(self, message, history, reference_history, **kwargs):
        received.update(kwargs)
        return "An answer."

    monkeypatch.setattr(ChatService, "chat", fake_chat)
    payload = {"message": "Hi", "history": [], "reference_history": []}
    if mode is not None:
        payload["mode"] = mode
    response = client.post("/chat/", json=payload, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert received["mode"] == mode


def test_atomic_chat_endpoint_never_422s_on_an_unknown_mode_string(client, monkeypatch):
    token = _register_and_login(client, email="mode-unknown-atomic@example.com")
    monkeypatch.setattr(ChatService, "chat", lambda self, *a, **k: "An answer.")
    response = client.post(
        "/chat/",
        json={"message": "Hi", "history": [], "reference_history": [], "mode": "not-a-real-mode"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


@pytest.mark.parametrize("mode", ["reasoning", "math", "coding", "planning", "document", None])
def test_stream_endpoint_passes_mode_through_unchanged(client, monkeypatch, mode):
    token = _register_and_login(client, email=f"mode-stream-{mode}@example.com")
    received = {}

    async def fake_stream(self, message, history, reference_history, **kwargs):
        received.update(kwargs)
        yield "An answer."

    monkeypatch.setattr(ChatService, "stream_chat", fake_stream)
    payload = {"message": "Hi", "history": [], "reference_history": []}
    if mode is not None:
        payload["mode"] = mode
    response = client.post(
        "/chat/stream", json=payload, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1] == {"type": "done"}
    assert received["mode"] == mode


def test_stream_endpoint_never_422s_on_an_unknown_mode_string(client, monkeypatch):
    token = _register_and_login(client, email="mode-unknown-stream@example.com")

    async def fake_stream(self, message, history, reference_history, **kwargs):
        yield "An answer."

    monkeypatch.setattr(ChatService, "stream_chat", fake_stream)
    response = client.post(
        "/chat/stream",
        json={"message": "Hi", "history": [], "reference_history": [], "mode": "bogus"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


def test_chat_profiles_endpoint_lists_the_six_public_profiles(client):
    token = _register_and_login(client, email="profiles-endpoint@example.com")
    response = client.get("/chat/profiles", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["default"] == "general"
    names = {profile["name"] for profile in body["profiles"]}
    assert names == {"general", "reasoning", "math", "coding", "planning", "document"}
    for profile in body["profiles"]:
        assert set(profile.keys()) == {"name", "label", "description", "thinking"}


def test_chat_profiles_endpoint_requires_authentication(client):
    response = client.get("/chat/profiles")
    assert response.status_code in (401, 403)
