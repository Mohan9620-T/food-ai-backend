from datetime import datetime, timezone

from cryptography.fernet import Fernet

from app.config import settings
from app.database.database import SessionLocal
from app.models.mail_credential import MailCredential


class MailTokenStore:
    def __init__(self, session_factory=SessionLocal):
        self.session_factory = session_factory

    @staticmethod
    def _cipher() -> Fernet:
        return Fernet(settings.EMAIL_TOKEN_ENCRYPTION_KEY.encode("ascii"))

    def get(self, configuration_id: str, initial_token: str) -> str:
        with self.session_factory() as db:
            row = db.get(MailCredential, configuration_id)
            if row is None:
                return initial_token
            return self._cipher().decrypt(row.encrypted_token.encode("ascii")).decode("utf-8")

    def save(self, configuration_id: str, token: str) -> None:
        encrypted = self._cipher().encrypt(token.encode("utf-8")).decode("ascii")
        with self.session_factory() as db:
            db.merge(
                MailCredential(
                    configuration_id=configuration_id,
                    encrypted_token=encrypted,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            db.commit()
