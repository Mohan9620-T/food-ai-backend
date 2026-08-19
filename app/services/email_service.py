import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import settings


logger = logging.getLogger(__name__)


class EmailService:
    def send_new_account_credentials(
        self,
        *,
        recipient: str,
        fullname: str,
        password: str,
    ) -> bool:
        if not settings.SMTP_HOST or not settings.SMTP_FROM_EMAIL:
            logger.info("SMTP is not configured; account email was not sent")
            return False

        message = EmailMessage()
        message["Subject"] = "Your Food AI Assistant account"
        message["From"] = settings.SMTP_FROM_EMAIL
        message["To"] = recipient
        message.set_content(
            f"Hello {fullname},\n\n"
            "Your Food AI Assistant account has been created.\n\n"
            f"Email: {recipient}\n"
            f"Password: {password}\n\n"
            "For your security, do not forward this email.\n"
        )

        try:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
                if settings.SMTP_USE_TLS:
                    smtp.starttls(context=ssl.create_default_context())
                if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
                    smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException):
            logger.exception("Could not send account email to %s", recipient)
            return False

        return True
