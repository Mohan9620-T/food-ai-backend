import re
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.models.email_otp import EmailOtp
from app.models.user import User
from app.services.email_service import EmailService
from app.utils.security import hash_refresh_token

CREDENTIALS = {
    "fullname": "New Account",
    "email": "new.account@example.com",
    "password": "chosen-secret",
}


@pytest.fixture
def otp_mail(monkeypatch):
    messages = []
    monkeypatch.setattr(EmailService, "configuration_errors", lambda self: [])
    monkeypatch.setattr(
        EmailService, "send_text", lambda self, **message: messages.append(message) or True
    )
    return messages


def _extract_code(body: str) -> str:
    return re.search(r"\b\d{6}\b", body)[0]


def _register(client, messages, email=CREDENTIALS["email"], password=CREDENTIALS["password"]):
    response = client.post(
        "/users/",
        json={"fullname": "New Account", "email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requires_otp"] is True
    otp_message = next(
        m
        for m in messages
        if m["recipient"] == email and m["subject"].startswith("Your Food AI verification")
    )
    return body, _extract_code(otp_message["body"])


@pytest.mark.no_auto_verify
def test_signup_requires_otp_and_does_not_log_the_user_in(client, otp_mail):
    body, code = _register(client, otp_mail)
    assert body["email"] == CREDENTIALS["email"]
    assert body["otp_email_sent"] is True

    # Correct password, but the account is not verified yet - no tokens, distinguishable error.
    blocked = client.post(
        "/users/login", json={"email": CREDENTIALS["email"], "password": CREDENTIALS["password"]}
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "email_not_verified"

    verified = client.post(
        "/users/verify-otp",
        json={"email": CREDENTIALS["email"], "otp": code, "purpose": "signup_verification"},
    )
    assert verified.status_code == 200, verified.text
    tokens = verified.json()
    assert "access_token" in tokens and "refresh_token" in tokens
    assert (
        client.get(
            "/chat/sessions", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        ).status_code
        == 200
    )


@pytest.mark.no_auto_verify
def test_otp_is_hashed_in_the_database_never_plaintext(client, db_session, otp_mail):
    _, code = _register(client, otp_mail)
    stored = db_session.query(EmailOtp).filter(EmailOtp.purpose == "signup_verification").one()
    assert stored.otp_hash == hash_refresh_token(code)
    assert stored.otp_hash != code
    assert len(stored.otp_hash) == 64  # sha256 hex digest, not the 6-digit code


@pytest.mark.no_auto_verify
def test_otp_never_appears_in_logs(client, otp_mail, caplog):
    caplog.set_level("DEBUG")
    _, code = _register(client, otp_mail)
    client.post(
        "/users/verify-otp",
        json={"email": CREDENTIALS["email"], "otp": "000000", "purpose": "signup_verification"},
    )
    client.post(
        "/users/verify-otp",
        json={"email": CREDENTIALS["email"], "otp": code, "purpose": "signup_verification"},
    )
    for record in caplog.records:
        assert code not in record.getMessage()
        assert code not in str(record.__dict__)


@pytest.mark.no_auto_verify
def test_wrong_otp_increments_attempts_and_locks_after_max(client, db_session, otp_mail):
    _, code = _register(client, otp_mail)
    for attempt in range(settings.OTP_MAX_ATTEMPTS):
        response = client.post(
            "/users/verify-otp",
            json={"email": CREDENTIALS["email"], "otp": "000000", "purpose": "signup_verification"},
        )
        assert response.status_code == 400

    # Even the correct code is now rejected: max attempts already reached.
    locked = client.post(
        "/users/verify-otp",
        json={"email": CREDENTIALS["email"], "otp": code, "purpose": "signup_verification"},
    )
    assert locked.status_code == 400
    assert (
        "new code" in locked.json()["detail"].lower()
        or "attempts" in locked.json()["detail"].lower()
    )


@pytest.mark.no_auto_verify
def test_expired_otp_is_rejected(client, db_session, otp_mail):
    _, code = _register(client, otp_mail)
    stored = db_session.query(EmailOtp).filter(EmailOtp.purpose == "signup_verification").one()
    stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    response = client.post(
        "/users/verify-otp",
        json={"email": CREDENTIALS["email"], "otp": code, "purpose": "signup_verification"},
    )
    assert response.status_code == 400


@pytest.mark.no_auto_verify
def test_resend_invalidates_the_prior_code(client, otp_mail, monkeypatch):
    codes = iter([123456, 654321])
    monkeypatch.setattr("app.services.email_otp_service.secrets.randbelow", lambda _: next(codes))
    _, first_code = _register(client, otp_mail)
    resend = client.post(
        "/users/resend-otp", json={"email": CREDENTIALS["email"], "purpose": "signup_verification"}
    )
    assert resend.status_code == 200
    second_message = otp_mail[-1]
    second_code = _extract_code(second_message["body"])
    assert second_code != first_code

    stale = client.post(
        "/users/verify-otp",
        json={"email": CREDENTIALS["email"], "otp": first_code, "purpose": "signup_verification"},
    )
    assert stale.status_code == 400

    fresh = client.post(
        "/users/verify-otp",
        json={"email": CREDENTIALS["email"], "otp": second_code, "purpose": "signup_verification"},
    )
    assert fresh.status_code == 200


def test_verified_users_second_login_never_prompts_for_otp(client):
    """The default fixture auto-verifies on signup - this is the normal, everyday path."""
    client.post("/users/", json=CREDENTIALS)
    first = client.post(
        "/users/login", json={"email": CREDENTIALS["email"], "password": CREDENTIALS["password"]}
    )
    assert first.status_code == 200
    second = client.post(
        "/users/login", json={"email": CREDENTIALS["email"], "password": CREDENTIALS["password"]}
    )
    assert second.status_code == 200
    assert "access_token" in second.json()


def test_forgot_password_otp_end_to_end_and_revokes_old_sessions(client, otp_mail):
    client.post("/users/", json=CREDENTIALS)
    old_login = client.post(
        "/users/login", json={"email": CREDENTIALS["email"], "password": CREDENTIALS["password"]}
    ).json()
    old_refresh = old_login["refresh_token"]

    request = client.post("/users/forgot-password", json={"email": CREDENTIALS["email"]})
    assert request.status_code == 200
    reset_message = next(
        m
        for m in otp_mail
        if m["recipient"] == CREDENTIALS["email"] and "reset" in m["subject"].lower()
    )
    code = _extract_code(reset_message["body"])
    assert CREDENTIALS["password"] not in reset_message["body"]

    reset = client.post(
        "/users/reset-password-otp",
        json={"email": CREDENTIALS["email"], "otp": code, "new_password": "brand-new-secret"},
    )
    assert reset.status_code == 200

    # Old refresh token must no longer work after a password reset.
    assert client.post("/users/refresh", json={"refresh_token": old_refresh}).status_code == 401
    # Old password must no longer work.
    assert (
        client.post(
            "/users/login",
            json={"email": CREDENTIALS["email"], "password": CREDENTIALS["password"]},
        ).status_code
        == 401
    )
    # New password works.
    relogin = client.post(
        "/users/login", json={"email": CREDENTIALS["email"], "password": "brand-new-secret"}
    )
    assert relogin.status_code == 200


def test_forgot_password_does_not_leak_whether_the_email_exists(client, otp_mail):
    client.post("/users/", json=CREDENTIALS)
    otp_mail.clear()  # ignore the welcome + signup-verification emails from registering
    existing = client.post("/users/forgot-password", json={"email": CREDENTIALS["email"]})
    missing = client.post("/users/forgot-password", json={"email": "nobody-here@example.com"})
    assert existing.status_code == missing.status_code == 200
    assert existing.json() == missing.json()
    assert len(otp_mail) == 1  # only the real account actually received a code


def test_password_is_never_included_in_any_otp_email(client, otp_mail):
    client.post("/users/", json=CREDENTIALS)
    for message in otp_mail:
        assert CREDENTIALS["password"] not in message["body"]


@pytest.mark.no_auto_verify
@pytest.mark.parametrize("welcome_sent,otp_sent", [(True, False), (False, True), (False, False)])
def test_registration_reports_otp_delivery_independently(
    client, db_session, monkeypatch, welcome_sent, otp_sent
):
    monkeypatch.setattr(
        EmailService, "send_new_account_welcome", lambda *args, **kwargs: welcome_sent
    )
    monkeypatch.setattr(EmailService, "send_otp_email", lambda *args, **kwargs: otp_sent)
    response = client.post("/users/", json=CREDENTIALS)
    assert response.status_code == 200
    assert response.json()["email_sent"] is welcome_sent
    assert response.json()["otp_email_sent"] is otp_sent
    assert response.json()["requires_otp"] is True
    assert db_session.query(EmailOtp).count() == int(otp_sent)
    assert not db_session.query(User).filter_by(email=CREDENTIALS["email"]).one().is_email_verified
    blocked = client.post(
        "/users/login", json={"email": CREDENTIALS["email"], "password": CREDENTIALS["password"]}
    )
    assert blocked.status_code == 403


@pytest.mark.no_auto_verify
def test_failed_resend_keeps_the_previous_code_usable(client, db_session, otp_mail, monkeypatch):
    _, code = _register(client, otp_mail)
    monkeypatch.setattr(EmailService, "send_otp_email", lambda *args, **kwargs: False)
    result = client.post(
        "/users/resend-otp", json={"email": CREDENTIALS["email"], "purpose": "signup_verification"}
    )
    assert result.status_code == 200
    assert "requested" in result.json()["detail"]
    stored = db_session.query(EmailOtp).one()
    assert stored.consumed_at is None
    assert stored.otp_hash == hash_refresh_token(code)
    assert (
        client.post(
            "/users/verify-otp",
            json={
                "email": CREDENTIALS["email"],
                "otp": code,
            },
        ).status_code
        == 200
    )


@pytest.mark.parametrize("endpoint", ["resend-otp", "forgot-password"])
def test_missing_sender_config_is_reported_without_revealing_accounts(
    client, monkeypatch, endpoint
):
    client.post("/users/", json=CREDENTIALS)
    monkeypatch.setattr(
        EmailService, "configuration_errors", lambda self: ["GMAIL_REFRESH_TOKEN is required."]
    )
    purpose = {"purpose": "signup_verification"} if endpoint == "resend-otp" else {}
    existing = client.post(f"/users/{endpoint}", json={"email": CREDENTIALS["email"], **purpose})
    missing = client.post(f"/users/{endpoint}", json={"email": "nobody@example.com", **purpose})
    assert existing.status_code == missing.status_code == 503
    assert existing.json() == missing.json()
    assert "unavailable" in existing.json()["detail"]
    assert "GMAIL" not in existing.text


@pytest.mark.no_auto_verify
@pytest.mark.parametrize("endpoint", ["resend-otp", "forgot-password"])
def test_rejected_send_acknowledgement_does_not_reveal_account(
    client, otp_mail, monkeypatch, endpoint
):
    _register(client, otp_mail)
    monkeypatch.setattr(EmailService, "send_otp_email", lambda *args, **kwargs: False)
    purpose = {"purpose": "signup_verification"} if endpoint == "resend-otp" else {}
    existing = client.post(f"/users/{endpoint}", json={"email": CREDENTIALS["email"], **purpose})
    missing = client.post(f"/users/{endpoint}", json={"email": "nobody@example.com", **purpose})
    assert existing.status_code == missing.status_code == 200
    assert existing.json() == missing.json()
    assert "has been sent" not in existing.text


def test_email_otps_are_scoped_to_user_and_purpose(db_session):
    """A direct model sanity check for the unique-active-per-purpose index."""
    from app.repositories.email_otp_repository import EmailOtpRepository
    from app.utils.security import hash_password

    user = User(
        fullname="Direct",
        email="direct@example.com",
        password=hash_password("x"),
        is_email_verified=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    repo = EmailOtpRepository()
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    repo.create(
        db_session, user.id, "signup_verification", hash_refresh_token("111111"), expires, 5
    )
    repo.create(db_session, user.id, "password_reset", hash_refresh_token("222222"), expires, 5)
    assert repo.get_active(
        db_session, user.id, "signup_verification"
    ).otp_hash == hash_refresh_token("111111")
    assert repo.get_active(db_session, user.id, "password_reset").otp_hash == hash_refresh_token(
        "222222"
    )

    repo.invalidate_active(db_session, user.id, "signup_verification")
    assert repo.get_active(db_session, user.id, "signup_verification") is None
    assert repo.get_active(db_session, user.id, "password_reset") is not None
