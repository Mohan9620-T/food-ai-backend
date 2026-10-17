import logging
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database.database import get_db
from app.rate_limit import limiter
from app.schemas.user import (
    ForgotPasswordRequest,
    RefreshTokenRequest,
    ResendOtpRequest,
    ResetPasswordOtpRequest,
    SetPasswordRequest,
    Token,
    UserCreate,
    UserLogin,
    UserResponse,
    VerifySignupOtpRequest,
)
from app.services.email_otp_service import (
    OtpDeliveryUnavailableError,
    OtpInvalidError,
    OtpLockedError,
)
from app.services.user_service import EmailAlreadyExistsError, EmailNotVerifiedError, UserService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["Users"])
service = UserService()


def _require_email_otp_enabled() -> None:
    if not settings.email_otp_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Email OTP is disabled locally. Log in with your email and password."
                if settings.APP_ENVIRONMENT == "development" and settings.LOCAL_DISABLE_EMAIL_OTP
                else "Email OTP is disabled. Log in with your email and password."
            ),
        )


@router.post("/set-password", summary="Choose a password using the private welcome-email link")
@limiter.limit(settings.LOGIN_RATE_LIMIT)
def set_password(request: Request, payload: SetPasswordRequest, db: Session = Depends(get_db)):
    if not service.account_passwords.set_password(db, payload.token, payload.password):
        raise HTTPException(
            status_code=400, detail="This password link is invalid, expired or already used."
        )
    logger.info("auth.password_changed")
    return {"detail": "Password saved. Log in with your new password."}


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
        403: {"description": "The account's first-login email verification is not complete."},
        422: {"description": "The login payload failed validation."},
        429: {"description": "The login rate limit was exceeded."},
    },
)
@limiter.limit(settings.LOGIN_RATE_LIMIT)
def login(request: Request, credentials: UserLogin, db: Session = Depends(get_db)):
    try:
        tokens = service.authenticate_user(
            db,
            credentials.email,
            credentials.password,
            credentials.remember_me,
        )
    except EmailNotVerifiedError:
        logger.info("auth.login_blocked", extra={"reason": "email_not_verified"})
        raise HTTPException(status_code=403, detail="email_not_verified") from None
    if not tokens:
        logger.info("auth.login_failed", extra={"reason": "invalid_credentials"})
        raise HTTPException(status_code=401, detail="Invalid email or password")

    logger.info("auth.login_succeeded")
    return Token(**tokens)


@router.post(
    "/verify-otp",
    dependencies=[Depends(_require_email_otp_enabled)],
    response_model=Token,
    summary="Verify the signup email code",
    description="Complete first login by verifying the 6-digit code emailed at signup.",
    responses={
        400: {"description": "The code is invalid, expired, or locked after too many attempts."},
        422: {"description": "The payload failed validation."},
        429: {"description": "The OTP verification rate limit was exceeded."},
    },
)
@limiter.limit(settings.OTP_RATE_LIMIT)
def verify_signup_otp(
    request: Request, payload: VerifySignupOtpRequest, db: Session = Depends(get_db)
):
    try:
        tokens = service.verify_signup_otp(db, payload.email, payload.otp)
    except (OtpInvalidError, OtpLockedError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    logger.info("auth.signup_verified")
    return Token(**cast(dict, tokens))


@router.post(
    "/resend-otp",
    dependencies=[Depends(_require_email_otp_enabled)],
    summary="Resend a verification code",
    description="Request a new verification code. Replace the old code only after provider acceptance.",
    responses={
        422: {"description": "The payload failed validation."},
        429: {"description": "The OTP resend rate limit was exceeded."},
        503: {"description": "Verification email delivery is not configured."},
    },
)
@limiter.limit(settings.OTP_RESEND_RATE_LIMIT)
def resend_otp(request: Request, payload: ResendOtpRequest, db: Session = Depends(get_db)):
    try:
        service.resend_otp(db, payload.email, payload.purpose)
    except OtpDeliveryUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    logger.info("auth.otp_requested")
    # Keep the same acknowledgement for unknown, already-verified and eligible
    # accounts. Provider acceptance does not establish inbox delivery.
    return {
        "detail": "Code requested. If this account needs verification, check your inbox and spam folder."
    }


@router.post(
    "/forgot-password",
    dependencies=[Depends(_require_email_otp_enabled)],
    summary="Request a password-reset code",
    description="Email a 6-digit password-reset code if the address has an account.",
    responses={
        422: {"description": "The payload failed validation."},
        429: {"description": "The forgot-password rate limit was exceeded."},
        503: {"description": "Verification email delivery is not configured."},
    },
)
@limiter.limit(settings.OTP_RATE_LIMIT)
def forgot_password(
    request: Request, payload: ForgotPasswordRequest, db: Session = Depends(get_db)
):
    try:
        service.forgot_password(db, payload.email)
    except OtpDeliveryUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    logger.info("auth.password_reset_requested")
    # Same response whether or not the email is registered - do not leak that.
    return {
        "detail": "Code requested. If this email has an account, check your inbox and spam folder."
    }


@router.post(
    "/reset-password-otp",
    dependencies=[Depends(_require_email_otp_enabled)],
    summary="Reset a password using an emailed code",
    description="Verify the 6-digit password-reset code and set a new password.",
    responses={
        400: {"description": "The code is invalid, expired, or locked after too many attempts."},
        422: {"description": "The payload failed validation."},
        429: {"description": "The password-reset rate limit was exceeded."},
    },
)
@limiter.limit(settings.OTP_RATE_LIMIT)
def reset_password_otp(
    request: Request, payload: ResetPasswordOtpRequest, db: Session = Depends(get_db)
):
    try:
        service.reset_password_with_otp(db, payload.email, payload.otp, payload.new_password)
    except (OtpInvalidError, OtpLockedError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    logger.info("auth.password_reset_with_otp")
    return {"detail": "Password saved. Log in with your new password."}


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
