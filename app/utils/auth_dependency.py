from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.models.user import User
from app.utils.security import decode_access_token

bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> dict:
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
    return payload
