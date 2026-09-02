import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database.database import get_db
from app.rate_limit import limiter
from app.schemas.user import RefreshTokenRequest, Token, UserCreate, UserLogin, UserResponse
from app.services.user_service import EmailAlreadyExistsError, UserService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["Users"])
service = UserService()


@router.post(
    "/",
    response_model=UserResponse,
    summary="Register a user",
    description="Create a user account with a unique email address.",
    responses={
        400: {"description": "An account already exists for the email address."},
        422: {"description": "The registration payload failed validation."},
        429: {"description": "The registration rate limit was exceeded."},
    },
)
@limiter.limit(settings.REGISTER_RATE_LIMIT)
def create_user(request: Request, user: UserCreate, db: Session = Depends(get_db)):
    try:
        created_user = service.create_user(db, user)
    except EmailAlreadyExistsError:
        logger.info("auth.registration_failed", extra={"reason": "email_exists"})
        raise HTTPException(status_code=400, detail="Email already exists")

    logger.info("auth.registration_succeeded", extra={"user_id": created_user["id"]})
    return created_user


@router.post(
    "/login",
    response_model=Token,
    summary="Log in",
    description="Validate user credentials and issue access and refresh tokens.",
    responses={
        401: {"description": "The email or password is incorrect."},
        422: {"description": "The login payload failed validation."},
        429: {"description": "The login rate limit was exceeded."},
    },
)
@limiter.limit(settings.LOGIN_RATE_LIMIT)
def login(request: Request, credentials: UserLogin, db: Session = Depends(get_db)):
    tokens = service.authenticate_user(
        db,
        credentials.email,
        credentials.password,
        credentials.remember_me,
    )
    if not tokens:
        logger.info("auth.login_failed", extra={"reason": "invalid_credentials"})
        raise HTTPException(status_code=401, detail="Invalid email or password")

    logger.info("auth.login_succeeded")
    return Token(**tokens)


@router.post(
    "/refresh",
    response_model=Token,
    summary="Refresh authentication tokens",
    description="Exchange a valid, active refresh token for a new token pair.",
    responses={
        401: {"description": "The refresh token is invalid, expired, or revoked."},
        422: {"description": "The refresh-token payload failed validation."},
    },
)
def refresh_token(payload: RefreshTokenRequest, db: Session = Depends(get_db)):
    tokens = service.refresh_access_token(db, payload.refresh_token)
    if not tokens:
        logger.info("auth.token_refresh_failed", extra={"reason": "invalid_or_revoked"})
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    logger.info("auth.token_refreshed")
    return Token(**tokens)


@router.post(
    "/logout",
    summary="Log out",
    description="Revoke the supplied refresh token. The operation is idempotent.",
    responses={422: {"description": "The refresh-token payload failed validation."}},
)
def logout(payload: RefreshTokenRequest, db: Session = Depends(get_db)):
    revoked = service.revoke_refresh_token(db, payload.refresh_token)
    logger.info("auth.token_revoked", extra={"revoked": revoked})
    return {"detail": "Logged out"}
