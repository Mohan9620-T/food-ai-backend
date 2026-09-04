from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.refresh_token import RefreshToken


class RefreshTokenRepository:
    def create(
        self,
        db: Session,
        user_id: int,
        token_hash: str,
        expires_at: datetime,
    ) -> RefreshToken:
        token = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        db.add(token)
        db.commit()
        db.refresh(token)
        return token

    def get_active(self, db: Session, token_hash: str) -> RefreshToken | None:
        return (
            db.query(RefreshToken)
            .filter(
                RefreshToken.token_hash == token_hash,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > datetime.now(timezone.utc),
            )
            .first()
        )

    def get_by_hash(self, db: Session, token_hash: str) -> RefreshToken | None:
        return db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()

    def revoke(self, db: Session, token: RefreshToken) -> None:
        setattr(token, "revoked_at", datetime.now(timezone.utc))
        db.commit()
