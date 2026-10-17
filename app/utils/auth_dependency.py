from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database.database import get_db
from app.models.user import User
from app.repositories.api_key_repository import ApiKeyRepository
from app.utils.security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> dict:
    if api_key is not None:
        row = ApiKeyRepository().authenticate(db, api_key) if settings.ENABLE_API_KEYS else None
        user = db.get(User, int(row.user_id)) if row is not None else None
        if user is None:
            raise HTTPException(401, "Invalid API key")
        return {
            "sub": str(user.id),
            "email": str(user.email),
            "fullname": str(user.fullname),
            "role": str(user.role),
            "auth_method": "api_key",
        }
    if credentials is None:
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    token = credentials.credentials
    payload = decode_access_token(token)
    if payload is not None:
        try:
            user_id = int(payload.get("sub", ""))
        except (ValueError, TypeError):
            user_id = -1
        user = db.get(User, user_id)
        if user is None or payload.get("auth_version", 0) != user.auth_version:
            payload = None
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    assert user is not None
    payload["role"] = str(user.role)
    return payload


def require_role(*allowed: str):
    def dependency(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
        api_key: str | None = Header(default=None, alias="X-API-Key"),
        db: Session = Depends(get_db),
    ) -> dict:
        if not settings.ENABLE_ROLES:
            return {}
        user = get_current_user(credentials, api_key, db)
        if user.get("role", "member") not in allowed:
            raise HTTPException(403, "Insufficient permissions")
        return user

    return dependency
