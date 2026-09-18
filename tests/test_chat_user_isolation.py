def test_chat_history_messages_and_documents_are_private_to_each_user(client):
    accounts = []
    for label in ("account-a", "account-b"):
        credentials = {"email": f"{label}@example.com", "password": "isolation-test-password"}
        assert client.post("/users/", json={**credentials, "fullname": label}).status_code == 200
        login = client.post("/users/login", json=credentials)
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        session = client.post("/chat/sessions", headers=headers, json={"title": label}).json()
        session_id = session["id"]
        imported = client.post(
            f"/chat/sessions/{session_id}/import",
            headers=headers,
            json={"messages": [{"sender": "user", "content": f"{label} private history"}]},
        )
        assert imported.status_code == 200
        document = client.post(
            "/chat/documents",
            headers=headers,
            data={"session_id": str(session_id), "analyze": "false"},
            files={"file": (f"{label}.txt", f"{label} private file".encode(), "text/plain")},
        )
        assert document.status_code == 200
        accounts.append(
            (
                headers,
                session_id,
                imported.json()["messages"][0]["id"],
                document.json()["attachment"]["id"],
            )
        )

    for owner, other in ((accounts[0], accounts[1]), (accounts[1], accounts[0])):
        headers, own_id, _, own_document = owner
        _, foreign_id, foreign_message, foreign_document = other
        sessions = client.get("/chat/sessions", headers=headers)
        assert [item["id"] for item in sessions.json()] == [own_id]
        assert client.get(f"/chat/sessions/{own_id}", headers=headers).status_code == 200
        assert (
            client.get(f"/chat/documents/{own_document}/download", headers=headers).status_code
            == 200
        )
        for suffix in ("data", "preview", "download"):
            assert (
                client.get(
                    f"/chat/documents/{foreign_document}/{suffix}", headers=headers
                ).status_code
                == 404
            )
        assert client.get(f"/chat/sessions/{foreign_id}", headers=headers).status_code == 404
        assert (
            client.put(
                f"/chat/sessions/{foreign_id}", headers=headers, json={"title": "changed"}
            ).status_code
            == 404
        )
        assert client.delete(f"/chat/sessions/{foreign_id}", headers=headers).status_code == 404
        assert (
            client.delete(
                f"/chat/sessions/{foreign_id}/messages/{foreign_message}", headers=headers
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/chat/sessions/{foreign_id}/import", headers=headers, json={"messages": []}
            ).status_code
            == 404
        )
        for route in ("/chat/", "/chat/stream"):
            assert (
                client.post(
                    f"{route}?session_id={foreign_id}",
                    headers=headers,
                    json={"message": "Read this chat"},
                ).status_code
                == 404
            )
        assert (
            client.post(
                "/chat/documents/automate",
                headers=headers,
                json={"session_id": foreign_id, "instruction": "Create a PDF", "confirm": False},
            ).status_code
            == 404
        )

    assert client.get("/chat/sessions").status_code in (401, 403)
    assert client.get(f"/chat/documents/{accounts[0][3]}/download").status_code in (401, 403)
