import base64
import json
import smtplib
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.mail_credential import MailCredential
from app.services.email_service import EmailService
from app.services.mail_token_store import MailTokenStore


@pytest.fixture(autouse=True)
def clean_mail_settings(monkeypatch):
    for name in (
        "SMTP_HOST",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_FROM_EMAIL",
        "EMAIL_FROM_EMAIL",
        "RESEND_API_KEY",
        "GMAIL_CLIENT_ID",
        "GMAIL_CLIENT_SECRET",
        "GMAIL_REFRESH_TOKEN",
        "MICROSOFT_CLIENT_ID",
        "MICROSOFT_CLIENT_SECRET",
        "MICROSOFT_REFRESH_TOKEN",
        "EMAIL_TOKEN_ENCRYPTION_KEY",
    ):
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, "EMAIL_PROVIDER", "auto")
    monkeypatch.setattr(settings, "SMTP_USE_TLS", True)
    monkeypatch.setattr(settings, "SMTP_USE_SSL", False)
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "MICROSOFT_TENANT_ID", "common")


def welcome(service=None):
    return (service or EmailService()).send_new_account_welcome(
        recipient="new.user@example.com", fullname="New User"
    )


def configure(monkeypatch, provider):
    monkeypatch.setattr(settings, "EMAIL_PROVIDER", provider)
    monkeypatch.setattr(settings, "EMAIL_FROM_EMAIL", "sender@example.com")
    if provider in {"gmail", "microsoft"}:
        monkeypatch.setattr(settings, "EMAIL_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
        monkeypatch.setattr(settings, provider.upper() + "_CLIENT_ID", "client-id")
        monkeypatch.setattr(settings, provider.upper() + "_CLIENT_SECRET", "client-secret")
        monkeypatch.setattr(settings, provider.upper() + "_REFRESH_TOKEN", "initial-refresh")
    if provider == "resend":
        monkeypatch.setattr(settings, "RESEND_API_KEY", "resend-secret")


def http_server(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(
        "app.services.email_service.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_smtp_starttls_and_no_password_in_message(monkeypatch):
    sent = {}

    class FakeSmtp:
        def __init__(self, host, port, timeout, **kwargs):
            sent.update(host=host, port=port, timeout=timeout, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self, context):
            sent["tls"] = True

        def login(self, username, password):
            sent.update(username=username, smtp_password=password)

        def send_message(self, message):
            sent["message"] = message
            return {}

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_USERNAME", "mailer@example.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "smtp-secret")
    monkeypatch.setattr(settings, "SMTP_FROM_EMAIL", "mailer@example.com")
    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", FakeSmtp)
    assert welcome()
    assert sent["tls"] is True
    assert sent["message"]["To"] == "new.user@example.com"
    assert "account has been created successfully" in sent["message"].get_content()
    assert "smtp-secret" not in sent["message"].as_string()
    sent.clear()
    monkeypatch.setattr(settings, "SMTP_USE_SSL", True)
    monkeypatch.setattr(settings, "SMTP_PORT", 465)
    monkeypatch.setattr("app.services.email_service.smtplib.SMTP_SSL", FakeSmtp)
    assert welcome()
    assert "context" in sent and "tls" not in sent


def test_disabled_mail_does_not_connect(monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: pytest.fail("Unexpected connection"))
    assert not welcome()


@pytest.mark.parametrize(
    "provider,changes,expected",
    [
        ("unknown", {}, "EMAIL_PROVIDER"),
        ("resend", {}, "RESEND_API_KEY"),
        ("resend", {"EMAIL_FROM_EMAIL": "sender@@example.com"}, "EMAIL_FROM_EMAIL"),
        ("resend", {"EMAIL_FROM_EMAIL": "bad\nBcc: someone@example.com"}, "EMAIL_FROM_EMAIL"),
        ("smtp", {"SMTP_USERNAME": "sender@example.com"}, "both SMTP_USERNAME"),
        (
            "smtp",
            {
                "SMTP_USERNAME": "sender@example.com",
                "SMTP_PASSWORD": "secret",
                "SMTP_USE_TLS": False,
            },
            "requires SMTP_USE_TLS",
        ),
        ("smtp", {"SMTP_PORT": 0}, "SMTP_PORT"),
        ("microsoft", {"MICROSOFT_TENANT_ID": "../evil"}, "MICROSOFT_TENANT_ID"),
        ("microsoft", {}, "specific tenant ID"),
        ("gmail", {"EMAIL_TOKEN_ENCRYPTION_KEY": "invalid-key"}, "Fernet"),
    ],
)
def test_incomplete_configuration_is_actionable(monkeypatch, provider, changes, expected):
    monkeypatch.setattr(settings, "EMAIL_PROVIDER", provider)
    for key, value in changes.items():
        monkeypatch.setattr(settings, key, value)
    assert expected in " ".join(EmailService().configuration_errors())
    assert not welcome()


def test_resend_accepts_all_recipient_domains(monkeypatch):
    configure(monkeypatch, "resend")
    sent = []

    def handler(request):
        assert str(request.url) == "https://api.resend.com/emails"
        assert request.headers["authorization"] == "Bearer resend-secret"
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "provider-id"})

    http_server(monkeypatch, handler)
    for recipient in ("user@gmail.com", "user@outlook.com", "user@company.com"):
        assert EmailService().send_new_account_welcome(recipient=recipient, fullname="User")
    assert [msg["to"][0] for msg in sent] == [
        "user@gmail.com",
        "user@outlook.com",
        "user@company.com",
    ]


@pytest.mark.parametrize(
    "failure", ["401", "429", "503", "timeout", "missing_id", "redirect", "invalid_json"]
)
def test_provider_failure_does_not_retry_or_leak_secrets(monkeypatch, caplog, failure):
    configure(monkeypatch, "resend")
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("resend-secret should not be logged")
        if failure == "redirect":
            return httpx.Response(307, headers={"Location": "https://other.example"})
        if failure == "missing_id":
            return httpx.Response(200, json={})
        if failure == "invalid_json":
            return httpx.Response(200, text="resend-secret")
        return httpx.Response(int(failure), json={"error": "resend-secret"})

    http_server(monkeypatch, handler)
    assert not welcome()
    assert len(calls) == 1
    assert "resend-secret" not in caplog.text
    assert "new.user@example.com" not in caplog.text


def test_gmail_oauth_and_mime_content(monkeypatch, db_session):
    configure(monkeypatch, "gmail")
    store = MailTokenStore(sessionmaker(bind=db_session.bind))
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
            assert form["refresh_token"] == ["initial-refresh"]
            assert form["grant_type"] == ["refresh_token"]
            return httpx.Response(
                200, json={"access_token": "access-secret", "refresh_token": "rotated-refresh"}
            )
        assert str(request.url) == "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
        assert request.headers["authorization"] == "Bearer access-secret"
        raw = base64.urlsafe_b64decode(json.loads(request.content)["raw"])
        message = BytesParser(policy=policy.default).parsebytes(raw)
        assert message["To"] == "new.user@example.com"
        assert "account has been created" in message.get_content()
        return httpx.Response(200, json={"id": "gmail-message"})

    http_server(monkeypatch, handler)
    assert welcome(EmailService(store))
    assert len(calls) == 2
    row = db_session.query(MailCredential).one()
    assert "rotated-refresh" not in row.encrypted_token
    assert store.get(row.configuration_id, "initial-refresh") == "rotated-refresh"


@pytest.mark.parametrize("delegated", [True, False])
def test_microsoft_graph_personal_and_work_accounts(monkeypatch, db_session, delegated):
    configure(monkeypatch, "microsoft")
    if not delegated:
        monkeypatch.setattr(settings, "MICROSOFT_REFRESH_TOKEN", "")
        monkeypatch.setattr(settings, "MICROSOFT_TENANT_ID", "tenant-id")
    store = MailTokenStore(sessionmaker(bind=db_session.bind))

    def handler(request):
        if request.url.host == "login.microsoftonline.com":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["refresh_token" if delegated else "client_credentials"]
            return httpx.Response(200, json={"access_token": "access-secret"})
        assert request.url.host == "graph.microsoft.com"
        assert request.url.path == (
            "/v1.0/me/sendMail" if delegated else "/v1.0/users/sender@example.com/sendMail"
        )
        message = json.loads(request.content)["message"]
        assert message["toRecipients"] == [{"emailAddress": {"address": "new.user@example.com"}}]
        return httpx.Response(202)

    http_server(monkeypatch, handler)
    assert welcome(EmailService(store))


def test_rotated_tokens_survive_service_restart_and_are_isolated(monkeypatch, db_session):
    configure(monkeypatch, "microsoft")
    factory = sessionmaker(bind=db_session.bind)
    store = MailTokenStore(factory)
    store.save("config-a", "rotated-secret")
    assert MailTokenStore(factory).get("config-a", "old-secret") == "rotated-secret"
    assert store.get("config-b", "different-account-secret") == "different-account-secret"
    monkeypatch.setattr(settings, "EMAIL_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        store.get("config-a", "old-secret")


def test_registration_succeeds_when_email_is_rejected(client, monkeypatch):
    configure(monkeypatch, "resend")
    http_server(
        monkeypatch, lambda request: httpx.Response(403, json={"error": "sender not verified"})
    )
    result = client.post(
        "/users/",
        json={
            "fullname": "Mail Failure",
            "email": "mail-failure@example.com",
            "password": "strong-test-password",
        },
    )
    assert result.status_code == 200
    assert result.json()["email_sent"] is False
    assert "password" not in result.json()


def test_smtp_auth_failure_is_redacted(monkeypatch, caplog):
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_FROM_EMAIL", "sender@example.com")

    def fail(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"do-not-log-secret")

    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", fail)
    assert not welcome()
    assert "do-not-log-secret" not in caplog.text
