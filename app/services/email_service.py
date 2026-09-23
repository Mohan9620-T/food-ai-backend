import base64
import hashlib
import logging
import re
import smtplib
import ssl
from email.errors import MessageError
from email.headerregistry import Address
from email.message import EmailMessage
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.services.mail_token_store import MailTokenStore

logger = logging.getLogger(__name__)


class EmailService:
    """One configured sender can deliver to Gmail, Outlook or any other mail domain."""

    def __init__(self, token_store: MailTokenStore | None = None):
        self.token_store = token_store or MailTokenStore()

    @property
    def provider(self) -> str:
        if settings.EMAIL_PROVIDER == "auto":
            return "smtp" if settings.SMTP_HOST else "disabled"
        return settings.EMAIL_PROVIDER

    @property
    def sender(self) -> str:
        return settings.EMAIL_FROM_EMAIL or settings.SMTP_FROM_EMAIL or ""

    def configuration_errors(self) -> list[str]:
        """Return setting names and safe guidance, never credentials or their values."""
        provider = self.provider
        if provider == "disabled":
            return ["Email is disabled. Configure EMAIL_PROVIDER and its sender credentials."]
        required = {
            "smtp": ("SMTP_HOST",),
            "resend": ("RESEND_API_KEY",),
            "gmail": (
                "GMAIL_CLIENT_ID",
                "GMAIL_CLIENT_SECRET",
                "GMAIL_REFRESH_TOKEN",
                "EMAIL_TOKEN_ENCRYPTION_KEY",
            ),
            "microsoft": ("MICROSOFT_CLIENT_ID",),
        }
        if provider not in required:
            return ["EMAIL_PROVIDER must be auto, disabled, smtp, resend, gmail or microsoft."]
        errors = [
            f"{name} is required." for name in required[provider] if not getattr(settings, name)
        ]
        if not self.sender:
            errors.append("EMAIL_FROM_EMAIL (or SMTP_FROM_EMAIL) is required.")
        else:
            try:
                if (
                    not Address(addr_spec=self.sender).domain
                    or "\n" in self.sender
                    or "\r" in self.sender
                ):
                    raise ValueError()
            except (ValueError, MessageError):
                errors.append("EMAIL_FROM_EMAIL must be one email address without a display name.")
        if provider == "smtp":
            if not 1 <= settings.SMTP_PORT <= 65535:
                errors.append("SMTP_PORT must be between 1 and 65535.")
            if bool(settings.SMTP_USERNAME) != bool(settings.SMTP_PASSWORD):
                errors.append("Set both SMTP_USERNAME and SMTP_PASSWORD for authenticated SMTP.")
            if settings.SMTP_USERNAME and not (settings.SMTP_USE_TLS or settings.SMTP_USE_SSL):
                errors.append("Authenticated SMTP requires SMTP_USE_TLS or SMTP_USE_SSL.")
        if provider == "microsoft":
            if not re.fullmatch(r"[A-Za-z0-9.-]+", settings.MICROSOFT_TENANT_ID):
                errors.append(
                    "MICROSOFT_TENANT_ID must be a tenant ID, domain, common or consumers."
                )
            if settings.MICROSOFT_REFRESH_TOKEN:
                if not settings.EMAIL_TOKEN_ENCRYPTION_KEY:
                    errors.append(
                        "EMAIL_TOKEN_ENCRYPTION_KEY is required for delegated Microsoft OAuth."
                    )
            else:
                if not settings.MICROSOFT_CLIENT_SECRET:
                    errors.append(
                        "MICROSOFT_CLIENT_SECRET is required for Microsoft application authentication."
                    )
                if settings.MICROSOFT_TENANT_ID in {"common", "consumers", "organizations"}:
                    errors.append(
                        "Microsoft application authentication requires a specific tenant ID."
                    )
        if provider == "gmail" or (provider == "microsoft" and settings.MICROSOFT_REFRESH_TOKEN):
            try:
                Fernet(settings.EMAIL_TOKEN_ENCRYPTION_KEY.encode("ascii"))
            except (ValueError, UnicodeError):
                errors.append("EMAIL_TOKEN_ENCRYPTION_KEY must be a valid Fernet key.")
        return errors

    def send_new_account_welcome(
        self,
        *,
        recipient: str,
        fullname: str,
        password_link: str | None = None,
        login_url: str | None = None,
    ) -> bool:
        account_details = "Use the password you chose during registration.\n"
        if login_url:
            account_details += f"Log in: {login_url}\n"
        if password_link:
            account_details += (
                "\nTo choose a new password, open this private, one-use link within 30 minutes:\n"
                f"{password_link}\n"
                "This link signs out your other sessions after you save a new password.\n"
                "Do not forward this link. Your password is never included in email.\n"
            )
        return self.send_text(
            recipient=recipient,
            subject="Welcome to Food AI Assistant",
            body=(
                f"Hello {fullname},\n\n"
                "Your Food AI Assistant account has been created successfully.\n\n"
                f"You can log in anytime using this email address: {recipient}\n\n"
                f"{account_details}\n"
                "If you did not create this account, please ignore this email.\n"
            ),
        )

    def send_text(self, *, recipient: str, subject: str, body: str) -> bool:
        """True means provider accepted the request, not confirmed inbox delivery.

        Deliberately do not retry a send: a timeout may follow an accepted message.
        This method is internal; there is no public arbitrary-recipient mail route.
        """
        errors = self.configuration_errors()
        if errors:
            logger.info(
                "email.not_configured",
                extra={"email_provider": self.provider, "configuration_errors": errors},
            )
            return False
        try:
            message = EmailMessage()
            message["Subject"] = subject
            message["From"] = Address(settings.EMAIL_FROM_NAME, addr_spec=self.sender)
            message["To"] = Address(addr_spec=recipient)
            message.set_content(body)
            if self.provider == "smtp":
                self._send_smtp(message)
            else:
                # Fixed HTTPS hosts; redirects are disabled to protect bearer credentials.
                with httpx.Client(
                    timeout=settings.EMAIL_TIMEOUT_SECONDS, follow_redirects=False
                ) as client:
                    if self.provider == "resend":
                        response = client.post(
                            "https://api.resend.com/emails",
                            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                            json={
                                "from": str(message["From"]),
                                "to": [recipient],
                                "subject": subject,
                                "text": body,
                            },
                        )
                        response.raise_for_status()
                        if not self._json_object(response).get("id"):
                            raise ValueError("Missing provider message ID")
                    elif self.provider == "gmail":
                        token = self._oauth_token(client, "gmail")
                        response = client.post(
                            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                            headers={"Authorization": f"Bearer {token}"},
                            json={
                                "raw": base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
                            },
                        )
                        response.raise_for_status()
                        if not self._json_object(response).get("id"):
                            raise ValueError("Missing provider message ID")
                    else:
                        token = self._oauth_token(client, "microsoft")
                        mailbox = (
                            "me"
                            if settings.MICROSOFT_REFRESH_TOKEN
                            else "users/" + quote(self.sender, safe="")
                        )
                        response = client.post(
                            f"https://graph.microsoft.com/v1.0/{mailbox}/sendMail",
                            headers={"Authorization": f"Bearer {token}"},
                            json={
                                "message": {
                                    "subject": subject,
                                    "from": {"emailAddress": {"address": self.sender}},
                                    "body": {"contentType": "Text", "content": body},
                                    "toRecipients": [{"emailAddress": {"address": recipient}}],
                                },
                                "saveToSentItems": True,
                            },
                        )
                        response.raise_for_status()
                        if response.status_code != 202:
                            raise ValueError("Microsoft did not accept the message")
        except (
            OSError,
            ValueError,
            smtplib.SMTPException,
            httpx.HTTPError,
            SQLAlchemyError,
            InvalidToken,
            MessageError,
        ) as error:
            # Provider bodies, exception messages, recipients and token values are never logged.
            status = (
                error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
            )
            logger.warning(
                "email.delivery_failed",
                extra={
                    "email_provider": self.provider,
                    "error_type": type(error).__name__,
                    "provider_status": status,
                },
            )
            return False
        logger.info("email.accepted", extra={"email_provider": self.provider})
        return True

    @staticmethod
    def _send_smtp(message: EmailMessage) -> None:
        connection: smtplib.SMTP
        if settings.SMTP_USE_SSL:
            connection = smtplib.SMTP_SSL(
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                timeout=settings.EMAIL_TIMEOUT_SECONDS,
                context=ssl.create_default_context(),
            )
        else:
            connection = smtplib.SMTP(
                settings.SMTP_HOST, settings.SMTP_PORT, timeout=settings.EMAIL_TIMEOUT_SECONDS
            )
        with connection as smtp:
            if settings.SMTP_USE_TLS and not settings.SMTP_USE_SSL:
                smtp.starttls(context=ssl.create_default_context())
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            if smtp.send_message(message):
                raise smtplib.SMTPException("Recipient refused")

    def _oauth_token(self, client: httpx.Client, provider: str) -> str:
        if provider == "gmail":
            endpoint = "https://oauth2.googleapis.com/token"
            client_id = settings.GMAIL_CLIENT_ID
            secret = settings.GMAIL_CLIENT_SECRET
            initial = settings.GMAIL_REFRESH_TOKEN
        else:
            endpoint = f"https://login.microsoftonline.com/{settings.MICROSOFT_TENANT_ID}/oauth2/v2.0/token"
            client_id = settings.MICROSOFT_CLIENT_ID
            secret = settings.MICROSOFT_CLIENT_SECRET
            initial = settings.MICROSOFT_REFRESH_TOKEN
        data = {"client_id": client_id}
        if secret:
            data["client_secret"] = secret
        configuration_id = hashlib.sha256(
            f"{endpoint}\0{client_id}\0{initial}".encode()
        ).hexdigest()
        if initial:
            data.update(
                grant_type="refresh_token",
                refresh_token=self.token_store.get(configuration_id, initial),
            )
        else:
            data.update(
                grant_type="client_credentials", scope="https://graph.microsoft.com/.default"
            )
        response = client.post(endpoint, data=data)
        response.raise_for_status()
        payload = self._json_object(response)
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("Missing access token")
        refreshed = payload.get("refresh_token")
        if initial and isinstance(refreshed, str) and refreshed:
            self.token_store.save(configuration_id, refreshed)
        return token

    @staticmethod
    def _json_object(response: httpx.Response) -> dict:
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Invalid provider response")
        return payload
