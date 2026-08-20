from app.services.email_service import EmailService


def test_send_new_account_welcome_uses_configured_smtp(monkeypatch):
    sent = {}

    class FakeSmtp:
        def __init__(self, host, port, timeout):
            sent.update(host=host, port=port, timeout=timeout)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            pass

        def starttls(self, context):
            sent["tls"] = True

        def login(self, username, password):
            sent.update(username=username, smtp_password=password)

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr("app.services.email_service.settings.SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr("app.services.email_service.settings.SMTP_PORT", 587)
    monkeypatch.setattr("app.services.email_service.settings.SMTP_USERNAME", "mailer@example.com")
    monkeypatch.setattr("app.services.email_service.settings.SMTP_PASSWORD", "smtp-secret")
    monkeypatch.setattr("app.services.email_service.settings.SMTP_FROM_EMAIL", "mailer@example.com")
    monkeypatch.setattr("app.services.email_service.settings.SMTP_USE_TLS", True)
    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", FakeSmtp)

    result = EmailService().send_new_account_welcome(
        recipient="new.user@example.com",
        fullname="New User",
    )

    assert result is True
    assert sent["host"] == "smtp.example.com"
    assert sent["tls"] is True
    assert sent["message"]["To"] == "new.user@example.com"
    assert "account has been created successfully" in sent["message"].get_content()
    assert "Password" not in sent["message"].get_content()


def test_send_new_account_welcome_skips_when_not_configured(monkeypatch):
    monkeypatch.setattr("app.services.email_service.settings.SMTP_HOST", None)

    assert EmailService().send_new_account_welcome(
        recipient="new.user@example.com",
        fullname="New User",
    ) is False