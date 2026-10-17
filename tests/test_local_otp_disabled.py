import pytest

from app.config import settings
from app.models.email_otp import EmailOtp
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.services.email_service import EmailService

pytestmark = pytest.mark.no_auto_verify

EMAIL = "local.login@example.com"
PASSWORD = "local-test-password"


@pytest.fixture(autouse=True)
def disable_local_otp(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "LOCAL_DISABLE_EMAIL_OTP", True)

    def unexpected_otp_email(*args, **kwargs):
        pytest.fail("Local password login must not send an OTP email")

    monkeypatch.setattr(EmailService, "send_otp_email", unexpected_otp_email)


def test_local_signup_allows_password_login_without_sending_otp(client, db_session):
    registered = client.post(
        "/users/", json={"fullname": "Local test", "email": EMAIL, "password": PASSWORD}
    )
    assert registered.status_code == 200
    assert registered.json()["requires_otp"] is False
    assert registered.json()["otp_email_sent"] is False
    assert db_session.query(EmailOtp).count() == 0

    login = client.post("/users/login", json={"email": EMAIL, "password": PASSWORD})
    assert login.status_code == 200
    token = login.json()["access_token"]
    assert (
        client.get("/chat/sessions", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )
    # Disabling verification must not falsely mark the email as verified.
    assert db_session.query(User).filter_by(email=EMAIL).one().is_email_verified is False


def test_existing_unverified_account_can_log_in_but_wrong_password_is_rejected(client, db_session):
    user = UserRepository().create_user(db_session, "Local test", EMAIL, PASSWORD)
    assert user.is_email_verified is False
    assert (
        client.post("/users/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 200
    )
    assert (
        client.post("/users/login", json={"email": EMAIL, "password": "wrong-password"}).status_code
        == 401
    )


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_local_override_cannot_disable_verification_in_deployed_environments(
    client, db_session, monkeypatch, environment
):
    UserRepository().create_user(db_session, "Local test", EMAIL, PASSWORD)
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", environment)
    login = client.post("/users/login", json={"email": EMAIL, "password": PASSWORD})
    assert login.status_code == 403
    assert login.json()["detail"] == "email_not_verified"


def test_disabling_override_restores_verification_requirement(client, db_session, monkeypatch):
    UserRepository().create_user(db_session, "Local test", EMAIL, PASSWORD)
    monkeypatch.setattr(settings, "LOCAL_DISABLE_EMAIL_OTP", False)
    assert (
        client.post("/users/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 403
    )


@pytest.mark.parametrize(
    "endpoint,payload",
    [
        ("verify-otp", {"email": EMAIL, "otp": "000000", "purpose": "signup_verification"}),
        ("resend-otp", {"email": EMAIL, "purpose": "signup_verification"}),
        ("forgot-password", {"email": EMAIL}),
        ("reset-password-otp", {"email": EMAIL, "otp": "000000", "new_password": "new-secret"}),
    ],
)
def test_otp_endpoints_are_disabled_without_changing_the_password(
    client, db_session, endpoint, payload
):
    user = UserRepository().create_user(db_session, "Local test", EMAIL, PASSWORD)
    original_hash = user.password
    response = client.post(f"/users/{endpoint}", json=payload)
    assert response.status_code == 503
    assert "disabled locally" in response.json()["detail"]
    assert db_session.query(EmailOtp).count() == 0
    db_session.refresh(user)
    assert user.password == original_hash
