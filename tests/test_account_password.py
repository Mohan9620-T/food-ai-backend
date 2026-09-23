from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from app.config import settings
from app.models.account_password_link import AccountPasswordLink
from app.models.user import User
from app.services.account_password_service import public_app_url
from app.services.email_service import EmailService
from app.utils.security import hash_refresh_token, verify_password


@pytest.fixture
def welcome_mail(monkeypatch):
    messages = []
    monkeypatch.setattr(settings, "PUBLIC_APP_URL", "https://food.example.com")
    monkeypatch.setattr(
        EmailService, "send_text", lambda self, **message: messages.append(message) or True
    )
    return messages


def register(client, messages):
    response = client.post(
        "/users/",
        json={
            "fullname": "New Account",
            "email": "new.account@example.com",
            "password": "chosen-secret",
        },
        headers={"Host": "untrusted.example.com"},
    )
    assert response.status_code == 200
    assert response.json()["email_sent"] is True
    body = messages[0]["body"]
    link = next(line for line in body.splitlines() if "/set-password#" in line)
    token = parse_qs(urlsplit(link).fragment)["token"][0]
    assert "chosen-secret" not in body
    assert "untrusted.example.com" not in body
    assert link.startswith("https://food.example.com/set-password#token=")
    assert token not in response.text
    assert messages[0]["recipient"] == "new.account@example.com"
    return token


def test_new_user_email_link_is_hashed_expiring_and_single_use(client, db_session, welcome_mail):
    token = register(client, welcome_mail)
    stored = db_session.query(AccountPasswordLink).one()
    assert stored.token_hash == hash_refresh_token(token)
    assert stored.token_hash != token
    expires = stored.expires_at.replace(tzinfo=timezone.utc)
    assert 29 * 60 < (expires - datetime.now(timezone.utc)).total_seconds() <= 30 * 60
    login = client.post(
        "/users/login", json={"email": "new.account@example.com", "password": "chosen-secret"}
    ).json()
    assert (
        client.get(
            "/chat/sessions", headers={"Authorization": "Bearer " + login["access_token"]}
        ).status_code
        == 200
    )
    payload = {"token": token, "password": "new-chosen-secret"}
    response = client.post("/users/set-password", json=payload)
    assert response.status_code == 200
    assert "new-chosen-secret" not in response.text
    assert client.post("/users/set-password", json=payload).status_code == 400
    assert (
        client.post(
            "/users/login", json={"email": "new.account@example.com", "password": "chosen-secret"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/users/login",
            json={"email": "new.account@example.com", "password": "new-chosen-secret"},
        ).status_code
        == 200
    )
    assert (
        client.post("/users/refresh", json={"refresh_token": login["refresh_token"]}).status_code
        == 401
    )
    assert (
        client.get(
            "/chat/sessions", headers={"Authorization": "Bearer " + login["access_token"]}
        ).status_code
        == 401
    )
    db_session.expire_all()
    user = db_session.query(User).one()
    assert verify_password("new-chosen-secret", user.password)
    assert user.auth_version == 1
    assert welcome_mail[-1]["subject"] == "Your Food AI password was changed"
    assert "new-chosen-secret" not in welcome_mail[-1]["body"]


def test_expired_or_wrong_links_do_not_change_password(client, db_session, welcome_mail):
    token = register(client, welcome_mail)
    row = db_session.query(AccountPasswordLink).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    for value in (token, "a" * 64):
        assert (
            client.post(
                "/users/set-password", json={"token": value, "password": "changed123"}
            ).status_code
            == 400
        )
    assert verify_password("chosen-secret", db_session.query(User).one().password)


@pytest.mark.parametrize("password", ["short", " " * 8, "a" * 73, "\u0b85" * 25])
def test_bad_password_does_not_consume_link(client, db_session, welcome_mail, password):
    token = register(client, welcome_mail)
    assert (
        client.post("/users/set-password", json={"token": token, "password": password}).status_code
        == 422
    )
    assert db_session.query(AccountPasswordLink).one().used_at is None


def test_welcome_failure_still_allows_chosen_password_login(client, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_APP_URL", "https://food.example.com")
    monkeypatch.setattr(EmailService, "send_text", lambda *args, **kwargs: False)
    response = client.post(
        "/users/",
        json={"fullname": "Account", "email": "delivery@example.com", "password": "secret123"},
    )
    assert response.status_code == 200
    assert response.json()["email_sent"] is False
    assert (
        client.post(
            "/users/login", json={"email": "delivery@example.com", "password": "secret123"}
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://food.example.com",
        "https://evil@food.example.com",
        "https://food.example.com?next=evil",
        "https://food.example.com#token=x",
        "https://food.example.com\n",
    ],
)
def test_account_link_rejects_untrusted_origin_settings(monkeypatch, url):
    monkeypatch.setattr(settings, "PUBLIC_APP_URL", url)
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    assert public_app_url() == ""


def test_password_endpoint_is_rate_limited(client):
    statuses = [
        client.post(
            "/users/set-password", json={"token": "a" * 64, "password": "secret123"}
        ).status_code
        for _ in range(6)
    ]
    assert statuses == [400] * 5 + [429]
