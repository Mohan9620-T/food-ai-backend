def test_create_user_success(client):
    response = client.post("/users/", json={
        "fullname": "Test User",
        "email": "test@example.com",
        "password": "test123"
    })

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@example.com"
    assert data["fullname"] == "Test User"
    assert "password" not in data


def test_create_user_duplicate_email_fails(client):
    payload = {
        "fullname": "Test User",
        "email": "duplicate@example.com",
        "password": "test123"
    }

    first = client.post("/users/", json=payload)
    assert first.status_code == 200

    second = client.post("/users/", json=payload)
    assert second.status_code == 400
    assert "already exists" in second.json()["detail"]


def test_login_success(client):
    client.post("/users/", json={
        "fullname": "Login User",
        "email": "login@example.com",
        "password": "mypassword"
    })

    response = client.post("/users/login", json={
        "email": "login@example.com",
        "password": "mypassword"
    })

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_wrong_password_fails(client):
    client.post("/users/", json={
        "fullname": "Login User",
        "email": "wrongpass@example.com",
        "password": "correctpassword"
    })

    response = client.post("/users/login", json={
        "email": "wrongpass@example.com",
        "password": "incorrectpassword"
    })

    assert response.status_code == 401


def test_login_nonexistent_user_fails(client):
    response = client.post("/users/login", json={
        "email": "doesnotexist@example.com",
        "password": "anything"
    })

    assert response.status_code == 401