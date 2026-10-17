from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database.database import get_db
from app.models.api_key import ApiKey
from app.rate_limit import limiter
from app.repositories.api_key_repository import ApiKeyRepository
from app.schemas.user import Token
from app.services import oauth_service
from app.utils.auth_dependency import get_current_user

router = APIRouter(prefix="/users", tags=["Accounts"])


class OAuthLogin(BaseModel):
    provider: Literal["google", "microsoft"]
    id_token: str = Field(min_length=1, max_length=16384, repr=False)


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r".*\S.*")


def keys_enabled():
    if not settings.ENABLE_API_KEYS:
        raise HTTPException(404, "API keys are not enabled")


@router.get("/auth-capabilities")
def capabilities():
    enabled = settings.ENABLE_OAUTH_LOGIN and bool(
        settings.OAUTH_FIREBASE_PROJECT_ID
        and settings.OAUTH_FIREBASE_API_KEY
        and settings.OAUTH_FIREBASE_AUTH_DOMAIN
    )
    return {
        "oauth": enabled,
        "password_reset": settings.email_otp_enabled(),
        "firebase": {
            "projectId": settings.OAUTH_FIREBASE_PROJECT_ID,
            "apiKey": settings.OAUTH_FIREBASE_API_KEY,
            "authDomain": settings.OAUTH_FIREBASE_AUTH_DOMAIN,
        }
        if enabled
        else None,
    }


@router.post("/oauth-login", response_model=Token)
@limiter.limit(settings.LOGIN_RATE_LIMIT)
def oauth_login(request: Request, payload: OAuthLogin, db: Session = Depends(get_db)):
    if not settings.ENABLE_OAUTH_LOGIN:
        raise HTTPException(404, "OAuth is not enabled")
    try:
        return oauth_service.login(
            db, payload.id_token, payload.provider, oauth_service.FirebaseVerifier()
        )
    except PermissionError:
        raise HTTPException(403, "A verified email address is required") from None
    except RuntimeError:
        raise HTTPException(503, "Sign-in is temporarily unavailable") from None
    except Exception:
        raise HTTPException(401, "Invalid or expired provider identity") from None


def public_key(row):
    return {
        key: getattr(row, key)
        for key in ("id", "name", "prefix", "created_at", "last_used_at", "revoked_at")
    }


@router.get("/keys", dependencies=[Depends(keys_enabled)])
def list_keys(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return [
        public_key(row)
        for row in db.query(ApiKey)
        .filter_by(user_id=int(user["sub"]))
        .order_by(ApiKey.id.desc())
        .all()
    ]


@router.post("/keys", status_code=201, dependencies=[Depends(keys_enabled)])
def create_key(
    payload: KeyCreate,
    response: Response,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    if user.get("auth_method") == "api_key":
        raise HTTPException(403, "Use your signed-in session to create API keys")
    response.headers["Cache-Control"] = "no-store"
    row, raw = ApiKeyRepository().create(db, int(user["sub"]), payload.name)
    return {**public_key(row), "key": raw}


@router.delete("/keys/{key_id}", dependencies=[Depends(keys_enabled)])
def revoke_key(key_id: int, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    row = db.query(ApiKey).filter_by(id=key_id, user_id=int(user["sub"])).first()
    if row is None:
        raise HTTPException(404, "API key not found")
    setattr(row, "revoked_at", datetime.now(timezone.utc))
    db.commit()
    return {"revoked": True}
