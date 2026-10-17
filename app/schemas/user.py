from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


class PasswordInput(BaseModel):
    password: str = Field(min_length=6)

    @field_validator("password")
    @classmethod
    def valid_password(cls, value: str) -> str:
        if not value.strip() or len(value.encode("utf-8")) > 72:
            raise ValueError("Password must not be blank and must be at most 72 UTF-8 bytes.")
        return value


class UserCreate(PasswordInput):
    fullname: str
    email: EmailStr


class SetPasswordRequest(PasswordInput):
    token: str = Field(min_length=64, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class UserResponse(BaseModel):
    id: int
    fullname: str
    email: EmailStr
    email_sent: bool = False
    otp_email_sent: bool = False
    requires_otp: bool = True

    class Config:
        from_attributes = True


class UserLogin(BaseModel):
    email: EmailStr
    password: str
    remember_me: bool = False


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class OtpCode(BaseModel):
    otp: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class VerifySignupOtpRequest(OtpCode):
    email: EmailStr
    purpose: Literal["signup_verification"] = "signup_verification"


class ResendOtpRequest(BaseModel):
    email: EmailStr
    purpose: Literal["signup_verification", "password_reset"]


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordOtpRequest(OtpCode):
    email: EmailStr
    new_password: str = Field(min_length=6)

    @field_validator("new_password")
    @classmethod
    def valid_new_password(cls, value: str) -> str:
        if not value.strip() or len(value.encode("utf-8")) > 72:
            raise ValueError("Password must not be blank and must be at most 72 UTF-8 bytes.")
        return value
