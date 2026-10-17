from unittest.mock import Mock

import pytest

from app.config import settings
from app.models.account_password_link import AccountPasswordLink
from app.models.email_otp import EmailOtp
from app.models.user import User
from app.services.email_service import EmailService


@pytest.mark.no_auto_verify
def test_production_password_only_signup_login_and_no_mail(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ENABLE_EMAIL_OTP", False)
    monkeypatch.setattr(settings, "ENABLE_EMAIL_DELIVERY", False)
    welcome = Mock(side_effect=AssertionError("Welcome email must not be triggered"))
    monkeypatch.setattr(EmailService, "send_new_account_welcome", welcome)
    credentials = {"email": "password-only@example.com", "password": "a-safe-test-password"}
    registered = client.post("/users/", json={"fullname": "Password Only", **credentials})
    assert registered.status_code == 200
    assert registered.json()["requires_otp"] is False
    assert registered.json()["email_sent"] is False
    assert registered.json()["otp_email_sent"] is False
    assert db_session.query(EmailOtp).count() == 0
    assert db_session.query(AccountPasswordLink).count() == 0
    assert db_session.query(User).one().is_email_verified is False
    assert client.post("/users/login", json=credentials).status_code == 200
    assert client.post("/users/login", json={**credentials, "password": "wrong"}).status_code == 401
    assert client.get("/users/auth-capabilities").json()["password_reset"] is False
    assert (
        client.post("/users/forgot-password", json={"email": credentials["email"]}).status_code
        == 503
    )
    assert not welcome.called


def test_delivery_kill_switch_prevents_provider_calls(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_EMAIL_DELIVERY", False)
    monkeypatch.setattr(settings, "ENABLE_EMAIL_OTP", True)
    check = Mock(side_effect=AssertionError("No provider configuration or network access"))
    monkeypatch.setattr(EmailService, "configuration_errors", check)
    assert not EmailService().send_text(recipient="test@example.com", subject="Test", body="Test")
    assert not settings.email_otp_enabled()
    assert not check.called
