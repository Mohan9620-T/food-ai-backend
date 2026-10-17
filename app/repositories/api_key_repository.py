import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.api_key import ApiKey


class ApiKeyRepository:
    def create(self, db: Session, user_id: int, name: str) -> tuple[ApiKey, str]:
        raw = "fai-" + secrets.token_urlsafe(32)
        row = ApiKey(
            user_id=user_id,
            name=name.strip(),
            prefix=raw[:12],
            key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row, raw

    def authenticate(self, db: Session, raw: str) -> ApiKey | None:
        if not raw.startswith("fai-") or not 30 <= len(raw) <= 100:
            return None
        digest = hashlib.sha256(raw.encode()).hexdigest()
        row = db.query(ApiKey).filter_by(key_hash=digest).first()
        if row is None or not hmac.compare_digest(str(row.key_hash), digest) or row.revoked_at:
            return None
        setattr(row, "last_used_at", datetime.now(timezone.utc))
        db.commit()
        return row
