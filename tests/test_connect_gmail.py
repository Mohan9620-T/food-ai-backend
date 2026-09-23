import hashlib
import json
import subprocess
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from scripts import connect_gmail


@pytest.mark.parametrize("matching_sender", [True, False])
def test_oauth_checks_state_pkce_scope_and_sender(monkeypatch, matching_sender):
    observed = {}
    thread = None

    def open_browser(url):
        nonlocal thread
        query = parse_qs(urlsplit(url).query)
        observed.update(query)

        def callback():
            for state in ("wrong-state", query["state"][0]):
                target = (
                    query["redirect_uri"][0]
                    + "?"
                    + urlencode({"code": "private-code", "state": state})
                )
                try:
                    with urllib.request.urlopen(target) as response:
                        assert response.status == 200
                except urllib.error.HTTPError as error:
                    assert state == "wrong-state" and error.code == 400

        thread = threading.Thread(target=callback)
        thread.start()
        return True

    def provider(request):
        if request.url.path == "/token":
            body = parse_qs(request.content.decode())
            challenge = (
                connect_gmail.base64.urlsafe_b64encode(
                    hashlib.sha256(body["code_verifier"][0].encode()).digest()
                )
                .rstrip(b"=")
                .decode()
            )
            assert challenge == observed["code_challenge"][0]
            assert body["redirect_uri"] == observed["redirect_uri"]
            assert body["code"] == ["private-code"]
            return httpx.Response(
                200,
                json={
                    "refresh_token": "private-refresh",
                    "access_token": "private-access",
                    "scope": "openid email " + connect_gmail.SEND_SCOPE,
                },
            )
        assert request.url.host == "openidconnect.googleapis.com"
        assert request.headers["Authorization"] == "Bearer private-access"
        return httpx.Response(
            200,
            json={
                "email": "sender@example.com" if matching_sender else "wrong@example.com",
                "email_verified": True,
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        connect_gmail.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(provider), **kwargs),
    )
    monkeypatch.setattr(connect_gmail.webbrowser, "open", open_browser)
    client = {
        "client_id": "test.apps.googleusercontent.com",
        "client_secret": "private-client-secret",
    }
    if matching_sender:
        assert connect_gmail.authorize(client, "sender@example.com") == "private-refresh"
    else:
        with pytest.raises(ValueError, match="does not match"):
            connect_gmail.authorize(client, "sender@example.com")
    thread.join(timeout=5)
    assert observed["code_challenge_method"] == ["S256"]
    assert observed["access_type"] == ["offline"]


def test_cli_preserves_encryption_key_and_keeps_credentials_off_arguments(
    monkeypatch, tmp_path, capsys
):
    client = tmp_path / "private.json"
    client.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "test.apps.googleusercontent.com",
                    "client_secret": "private-client-secret",
                }
            }
        )
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "connect_gmail",
            "--client-json",
            str(client),
            "--sender",
            "sender@example.com",
            "--project",
            "project-id",
        ],
    )
    monkeypatch.setattr(connect_gmail.shutil, "which", lambda name: "railway")
    monkeypatch.setattr(connect_gmail, "authorize", lambda *args: "private-refresh")
    calls = []

    def railway(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            stdout=json.dumps({"EMAIL_TOKEN_ENCRYPTION_KEY": "existing-encryption-key"})
        )

    monkeypatch.setattr(connect_gmail.subprocess, "run", railway)
    assert connect_gmail.main() == 0
    assert len(calls) == 6
    assert all("EMAIL_TOKEN_ENCRYPTION_KEY" not in args for args, _ in calls)
    assert all(
        "private-refresh" not in args and "private-client-secret" not in args for args, _ in calls
    )
    assert all("--skip-deploys" in args and "--stdin" in args for args, _ in calls[1:])
    assert "private-refresh" not in capsys.readouterr().out


def test_railway_failure_does_not_print_secret_output(monkeypatch, tmp_path, capsys):
    client = tmp_path / "private.json"
    client.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "test.apps.googleusercontent.com",
                    "client_secret": "private-secret",
                }
            }
        )
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "connect_gmail",
            "--client-json",
            str(client),
            "--sender",
            "sender@example.com",
            "--project",
            "project-id",
        ],
    )
    monkeypatch.setattr(connect_gmail.shutil, "which", lambda name: "railway")

    def denied(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "railway", output="private-secret")

    monkeypatch.setattr(connect_gmail.subprocess, "run", denied)
    assert connect_gmail.main() == 1
    assert "private-secret" not in capsys.readouterr().out
