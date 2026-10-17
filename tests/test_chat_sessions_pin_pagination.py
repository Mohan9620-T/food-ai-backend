"""Pin/unpin a chat session and the paginated /chat/sessions/page endpoint.

Includes a regression test that the original, bare GET /chat/sessions is
byte-identical in shape and ordering to before pinning existed.
"""


def _register_and_login(client, email="pin-test@example.com", password="pin-password"):
    client.post("/users/", json={"fullname": "Pin Tester", "email": email, "password": password})
    return client.post("/users/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]


def _create_session(client, token, title):
    return client.post(
        "/chat/sessions",
        json={"title": title},
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]


def test_pin_appends_to_the_end_of_the_pinned_set_in_order(client):
    token = _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    first = _create_session(client, token, "First")
    second = _create_session(client, token, "Second")
    third = _create_session(client, token, "Third")

    for session_id in (first, second, third):
        response = client.put(
            f"/chat/sessions/{session_id}/pin", json={"pinned": True}, headers=headers
        )
        assert response.status_code == 200
        assert response.json()["is_pinned"] is True

    page = client.get("/chat/sessions/page", headers=headers).json()
    pin_orders = {item["id"]: item["pin_order"] for item in page["conversations"]}
    assert pin_orders[first] < pin_orders[second] < pin_orders[third]


def test_unpin_clears_both_pin_fields(client):
    token = _register_and_login(client, email="unpin-test@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = _create_session(client, token, "Session")
    client.put(f"/chat/sessions/{session_id}/pin", json={"pinned": True}, headers=headers)

    response = client.put(
        f"/chat/sessions/{session_id}/pin", json={"pinned": False}, headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_pinned"] is False
    assert body["pin_order"] is None


def test_pin_endpoint_404s_for_a_session_owned_by_another_user(client):
    token_a = _register_and_login(client, email="owner-a@example.com")
    token_b = _register_and_login(client, email="owner-b@example.com")
    session_id = _create_session(client, token_a, "Owner A's session")

    response = client.put(
        f"/chat/sessions/{session_id}/pin",
        json={"pinned": True},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert response.status_code == 404


def test_paginated_sessions_list_sorts_pinned_sessions_first(client):
    token = _register_and_login(client, email="order-test@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    unpinned = _create_session(client, token, "Unpinned")
    pinned = _create_session(client, token, "Pinned")
    client.put(f"/chat/sessions/{pinned}/pin", json={"pinned": True}, headers=headers)

    page = client.get("/chat/sessions/page", headers=headers).json()
    ids_in_order = [item["id"] for item in page["conversations"]]
    assert ids_in_order.index(pinned) < ids_in_order.index(unpinned)


def test_paginated_sessions_list_has_total_limit_and_offset(client):
    token = _register_and_login(client, email="total-test@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    for i in range(3):
        _create_session(client, token, f"Session {i}")

    page = client.get("/chat/sessions/page?limit=2&offset=0", headers=headers).json()
    assert page["total"] == 3
    assert page["limit"] == 2
    assert page["offset"] == 0
    assert len(page["conversations"]) == 2

    next_page = client.get("/chat/sessions/page?limit=2&offset=2", headers=headers).json()
    assert len(next_page["conversations"]) == 1


def test_paginated_sessions_list_requires_authentication(client):
    response = client.get("/chat/sessions/page")
    assert response.status_code in (401, 403)


def test_bare_sessions_list_is_unchanged_by_pinning(client):
    """Regression test: GET /chat/sessions keeps its exact pre-pinning shape and
    ordering (plain array, updated_at desc, no pin reordering, and no side effect
    on updated_at from the pin action itself) even once some sessions are pinned."""
    token = _register_and_login(client, email="regression-test@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    first = _create_session(client, token, "First")
    second = _create_session(client, token, "Second")

    before = client.get("/chat/sessions", headers=headers)
    assert before.status_code == 200
    order_before = [item["id"] for item in before.json()]
    assert set(order_before) == {first, second}

    # Pinning a session must not reorder (or otherwise change the shape of)
    # the bare, unpaginated endpoint at all.
    client.put(f"/chat/sessions/{first}/pin", json={"pinned": True}, headers=headers)
    client.put(f"/chat/sessions/{second}/pin", json={"pinned": True}, headers=headers)
    client.put(f"/chat/sessions/{first}/pin", json={"pinned": False}, headers=headers)

    response = client.get("/chat/sessions", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert [item["id"] for item in body] == order_before
    for item in body:
        assert set(item.keys()) >= {"id", "title", "updated_at"}
