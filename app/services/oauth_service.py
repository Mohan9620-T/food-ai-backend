"""Verify Firebase-issued Google/Microsoft ID tokens before trusting identity."""

import secrets
import time
from functools import lru_cache
from typing import Protocol

import jwt
from pydantic import EmailStr, TypeAdapter
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.services.user_service import UserService


class IdentityVerifier(Protocol):
    def verify(self, token: str, provider: str) -> dict: ...


@lru_cache(maxsize=1)
def signing_keys():
    return jwt.PyJWKClient(
        "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com",
        timeout=5,
    )


class FirebaseVerifier:
    def verify(self, token: str, provider: str) -> dict:
        project = settings.OAUTH_FIREBASE_PROJECT_ID
        if not project:
            raise RuntimeError("OAuth is not configured")
        key = signing_keys().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=project,
            issuer=f"https://securetoken.google.com/{project}",
            options={"require": ["exp", "iat", "sub", "aud", "iss", "auth_time"]},
        )
        if (
            not isinstance(claims["sub"], str)
            or not 1 <= len(claims["sub"]) <= 128
            or claims["auth_time"] > time.time()
        ):
            raise ValueError("Invalid identity")
        if (
            claims.get("firebase", {}).get("sign_in_provider")
            != {"google": "google.com", "microsoft": "microsoft.com"}[provider]
        ):
            raise ValueError("Provider mismatch")
        return claims


def login(db: Session, token: str, provider: str, verifier: IdentityVerifier) -> dict:
    claims = verifier.verify(token, provider)
    if claims.get("email_verified") is not True:
        raise PermissionError("A verified email address is required")
    email = str(TypeAdapter(EmailStr).validate_python(claims.get("email", ""))).casefold()
    user = db.query(User).filter(func.lower(User.email) == email).first()
    if user is None:
        try:
            user = UserRepository().create_user(
                db, str(claims.get("name") or email)[:100], email, secrets.token_urlsafe(32)
            )
        except IntegrityError:
            db.rollback()
            user = db.query(User).filter(func.lower(User.email) == email).one()
    setattr(user, "is_email_verified", True)
    db.commit()
    return UserService()._issue_tokens(db, user, remember_me=False)
