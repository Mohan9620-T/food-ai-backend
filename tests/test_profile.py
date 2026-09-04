def _headers(client, email="profile@example.com"):
    client.post(
        "/users/", json={"fullname": "Profile User", "email": email, "password": "profile-password"}
    )
    login = client.post("/users/login", json={"email": email, "password": "profile-password"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_profile_get_404_then_upsert_and_update(client):
    headers = _headers(client)
    assert client.get("/profile/", headers=headers).status_code == 404
    payload = {
        "goal": "lose_weight",
        "target_calories": 1800,
        "target_protein_g": 120,
        "target_carbs_g": None,
        "target_fat_g": 60,
        "allergies": ["peanuts", " peanuts "],
        "dietary_restrictions": ["vegetarian"],
        "disliked_foods": ["olives"],
    }
    created = client.put("/profile/", headers=headers, json=payload)
    assert created.status_code == 200
    assert created.json()["allergies"] == ["peanuts"]
    payload["goal"] = "maintain"
    payload["allergies"] = ["shellfish"]
    updated = client.put("/profile/", headers=headers, json=payload)
    assert updated.status_code == 200
    assert updated.json()["id"] == created.json()["id"]
    assert client.get("/profile/", headers=headers).json()["allergies"] == ["shellfish"]


def test_profile_requires_authentication(client):
    assert client.get("/profile/").status_code in (401, 403)
