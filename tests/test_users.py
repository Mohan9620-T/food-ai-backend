def test_create_user_success(client):
    response = client.post(
        "/users/",
        json={"fullname": "Test User", "email": "test@example.com", "password": "test123"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@example.com"
    assert data["fullname"] == "Test User"
    assert data["email_sent"] is False
    assert "password" not in data


def test_create_user_duplicate_email_fails(client):
    payload = {"fullname": "Test User", "email": "duplicate@example.com", "password": "test123"}

    first = client.post("/users/", json=payload)
    assert first.status_code == 200

    second = client.post("/users/", json=payload)
    assert second.status_code == 400
    assert "already exists" in second.json()["detail"]


def test_login_success(client):
    client.post(
        "/users/",
        json={"fullname": "Login User", "email": "login@example.com", "password": "mypassword"},
    )

    response = client.post(
        "/users/login", json={"email": "login@example.com", "password": "mypassword"}
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_login_wrong_password_fails(client):
    client.post(
        "/users/",
        json={
            "fullname": "Login User",
            "email": "wrongpass@example.com",
            "password": "correctpassword",
        },
    )

    response = client.post(
        "/users/login", json={"email": "wrongpass@example.com", "password": "incorrectpassword"}
    )

    assert response.status_code == 401


def test_login_nonexistent_user_fails(client):
    response = client.post(
        "/users/login", json={"email": "doesnotexist@example.com", "password": "anything"}
    )

    assert response.status_code == 401


def test_access_token_is_short_lived(client):
    from datetime import datetime, timezone

    from jose import jwt

    from app.utils.security import ALGORITHM, SECRET_KEY

    client.post(
        "/users/",
        json={
            "fullname": "Remembered User",
            "email": "remember@example.com",
            "password": "mypassword",
        },
    )

    response = client.post(
        "/users/login",
        json={
            "email": "remember@example.com",
            "password": "mypassword",
        },
    )

    payload = jwt.decode(response.json()["access_token"], SECRET_KEY, algorithms=[ALGORITHM])
    remaining_minutes = (
        datetime.fromtimestamp(payload["exp"], timezone.utc) - datetime.now(timezone.utc)
    ).total_seconds() / 60
    assert 0 < remaining_minutes <= 30


def test_remember_me_creates_long_lived_hashed_refresh_token(client, db_session):
    from datetime import datetime, timezone

    from app.models.refresh_token import RefreshToken
    from app.utils.security import hash_refresh_token

    client.post(
        "/users/",
        json={
            "fullname": "Remembered User",
            "email": "remember@example.com",
            "password": "mypassword",
        },
    )
    response = client.post(
        "/users/login",
        json={
            "email": "remember@example.com",
            "password": "mypassword",
            "remember_me": True,
        },
    )

    raw_token = response.json()["refresh_token"]
    stored_token = db_session.query(RefreshToken).one()
    expires_at = stored_token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    assert stored_token.token_hash == hash_refresh_token(raw_token)
    assert stored_token.token_hash != raw_token
    assert (expires_at - datetime.now(timezone.utc)).days >= 29


def test_refresh_and_logout_revoke_token(client, db_session):
    from app.models.refresh_token import RefreshToken

    client.post(
        "/users/",
        json={"fullname": "Refresh User", "email": "refresh@example.com", "password": "mypassword"},
    )
    login_response = client.post(
        "/users/login",
        json={
            "email": "refresh@example.com",
            "password": "mypassword",
        },
    )
    refresh_token = login_response.json()["refresh_token"]

    refresh_response = client.post("/users/refresh", json={"refresh_token": refresh_token})
    assert refresh_response.status_code == 200
    assert refresh_response.json()["refresh_token"] == refresh_token
    assert refresh_response.json()["access_token"]

    logout_response = client.post("/users/logout", json={"refresh_token": refresh_token})
    assert logout_response.status_code == 200
    assert db_session.query(RefreshToken).one().revoked_at is not None

    rejected_response = client.post("/users/refresh", json={"refresh_token": refresh_token})
    assert rejected_response.status_code == 401


def test_registration_is_rate_limited(client):
    responses = [
        client.post(
            "/users/",
            json={
                "fullname": f"Rate User {index}",
                "email": f"rate-{index}@example.com",
                "password": "mypassword",
            },
        )
        for index in range(6)
    ]

    assert [response.status_code for response in responses[:5]] == [200] * 5
    assert responses[5].status_code == 429
