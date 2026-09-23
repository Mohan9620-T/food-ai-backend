import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import settings
from app.models.account_password_link import AccountPasswordLink
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.email_service import EmailService
from app.utils.security import hash_password, hash_refresh_token

logger = logging.getLogger(__name__)


def public_app_url() -> str:
    """Never construct account links from the untrusted request Host header."""
    value = settings.PUBLIC_APP_URL
    try:
        parsed = urlsplit(value)
        local = settings.APP_ENVIRONMENT == "development" and parsed.hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
        if (
            not parsed.hostname
            or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(char.isspace() for char in value)
        ):
            return ""
        return value.rstrip("/")
    except ValueError:
        return ""


class AccountPasswordService:
    def __init__(self, email_service: EmailService | None = None):
        self.email_service = email_service or EmailService()

    def create_link(self, db: Session, user_id: int) -> str | None:
        base = public_app_url()
        if not base:
            logger.info(
                "email.password_link_disabled",
                extra={"reason": "PUBLIC_APP_URL_missing_or_invalid"},
            )
            return None
        token = secrets.token_urlsafe(48)
        db.add(
            AccountPasswordLink(
                token_hash=hash_refresh_token(token),
                user_id=user_id,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            )
        )
        db.commit()
        # Fragments are not sent in HTTP request URLs or Referer headers.
        return f"{base}/set-password#token={token}"

    def set_password(self, db: Session, token: str, password: str) -> bool:
        now = datetime.now(timezone.utc)
        digest = hash_refresh_token(token)
        # Atomic consumption prevents two requests from using the same link.
        claimed = db.execute(
            update(AccountPasswordLink)
            .where(
                AccountPasswordLink.token_hash == digest,
                AccountPasswordLink.used_at.is_(None),
                AccountPasswordLink.expires_at > now,
            )
            .values(used_at=now)
            .returning(AccountPasswordLink.user_id)
        ).scalar_one_or_none()
        if claimed is None:
            db.rollback()
            return False
        changed = db.execute(
            update(User)
            .where(User.id == claimed)
            .values(password=hash_password(password), auth_version=User.auth_version + 1)
            .returning(User.id)
        ).scalar_one_or_none()
        if changed is None:
            db.rollback()
            return False
        db.execute(
            update(RefreshToken).where(RefreshToken.user_id == claimed).values(revoked_at=now)
        )
        db.execute(
            update(AccountPasswordLink)
            .where(AccountPasswordLink.user_id == claimed)
            .values(used_at=now)
        )
        db.commit()
        user = db.get(User, claimed)
        if user is not None:
            self.email_service.send_text(
                recipient=str(user.email),
                subject="Your Food AI password was changed",
                body=(
                    "Your Food AI password was changed using your private account link.\n"
                    "Your previous sessions have been signed out.\n"
                    "If this was not you, contact your Food AI administrator.\n"
                ),
            )
        return True
