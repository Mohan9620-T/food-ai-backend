import os
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["MIGRATION_CHECK_ENABLED"] = "false"
os.environ["APP_ENVIRONMENT"] = "development"
os.environ["LLM_PROVIDER"] = "ollama"
os.environ["EMAIL_PROVIDER"] = "disabled"
os.environ["LOCAL_DISABLE_EMAIL_OTP"] = "false"
os.environ["ENABLE_WEB_SEARCH"] = "false"

from app.database.database import Base, get_db
from app.main import app
from app.rate_limit import limiter
from app.repositories.user_repository import UserRepository

# Use a fresh in-memory SQLite database for tests instead of the real Postgres DB.
TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def valid_png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (2, 2), color="red").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _auto_verify_new_users(request, monkeypatch):
    """Most tests only need "a logged-in user", not the signup-OTP flow itself.

    Mark a test `@pytest.mark.no_auto_verify` to exercise the real, unverified
    first-login flow (see tests/test_email_otp.py).
    """
    if "no_auto_verify" in request.keywords:
        return
    original_create_user = UserRepository.create_user

    def create_user_and_verify(self, db, fullname, email, password):
        user = original_create_user(self, db, fullname, email, password)
        user.is_email_verified = True
        db.commit()
        db.refresh(user)
        return user

    monkeypatch.setattr(UserRepository, "create_user", create_user_and_verify)


@pytest.fixture(scope="function")
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    limiter.reset()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
