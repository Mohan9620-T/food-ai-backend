"""Authorize a Gmail sender locally and install credentials directly in Railway.

Run this yourself after creating a Google OAuth Desktop client. No password, code,
refresh token or client secret is printed or written to an output file.
"""

import argparse
import base64
import hashlib
import json
import secrets
import shutil
import subprocess
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet

SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def authorize(client: dict, sender: str) -> str:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    result: dict[str, str] = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Callback URLs contain authorization codes. Never log them.

        def do_GET(self):
            params = parse_qs(urlsplit(self.path).query)
            valid = (
                urlsplit(self.path).path == "/"
                and secrets.compare_digest(params.get("state", [""])[0], state)
                and self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"
            )
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if not valid:
                self.wfile.write(b"Invalid callback. Return to the original Google sign-in window.")
                return
            result["code"] = params.get("code", [""])[0]
            result["error"] = params.get("error", [""])[0]
            self.wfile.write(
                b"Google authorization received. Return to PowerShell to see setup results. You may close this tab."
            )

    with HTTPServer(("127.0.0.1", 0), Callback) as server:
        redirect = f"http://127.0.0.1:{server.server_port}/"
        query = urlencode(
            {
                "client_id": client["client_id"],
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": f"openid email {SEND_SCOPE}",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",
                "prompt": "consent",
                "login_hint": sender,
            }
        )
        print("Opening Google consent. Sign in as the sender and allow sending email.")
        if not webbrowser.open("https://accounts.google.com/o/oauth2/v2/auth?" + query):
            raise ValueError(
                "Could not open the browser. Run this command in your desktop PowerShell."
            )
        server.timeout = 1
        deadline = time.monotonic() + 300
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if result.get("error") or not result.get("code"):
        raise ValueError(
            "Google authorization was declined or timed out. No credentials were installed."
        )
    with httpx.Client(timeout=20, follow_redirects=False) as http:
        response = http.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
                "code": result["code"],
                "code_verifier": verifier,
                "redirect_uri": redirect,
                "grant_type": "authorization_code",
            },
        )
        response.raise_for_status()
        tokens = response.json()
        if not tokens.get("refresh_token") or SEND_SCOPE not in tokens.get("scope", "").split():
            raise ValueError(
                "Google did not grant offline Gmail sending access. No credentials were installed."
            )
        identity = http.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={
                "Authorization": "Bearer " + tokens["access_token"],
            },
        )
        identity.raise_for_status()
        profile = identity.json()
        if (
            profile.get("email", "").casefold() != sender.casefold()
            or profile.get("email_verified") is not True
        ):
            raise ValueError(
                "The authorized Google account does not match the sender. No credentials were installed."
            )
        return str(tokens["refresh_token"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-json", type=Path, required=True)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--service", default="food-ai-backend")
    parser.add_argument("--environment", default="production")
    args = parser.parse_args()
    railway = shutil.which("railway")
    if not railway:
        parser.error("Install the Railway CLI and run railway login first.")
    target = [
        "--project",
        args.project,
        "--service",
        args.service,
        "--environment",
        args.environment,
    ]
    try:
        client = json.loads(args.client_json.read_text(encoding="utf-8-sig")).get("installed", {})
        if not client.get("client_secret") or not client.get("client_id", "").endswith(
            ".apps.googleusercontent.com"
        ):
            raise ValueError("Use the JSON downloaded for a Google OAuth Desktop app client.")
        # Fail before opening consent if Railway authentication/project access is missing.
        current = subprocess.run(
            [railway, "variable", "list", *target, "--json"],
            check=True,
            text=True,
            capture_output=True,
        )
        variables = json.loads(current.stdout)
        refresh = authorize(client, args.sender)
        updates = {
            "GMAIL_CLIENT_ID": client["client_id"],
            "GMAIL_CLIENT_SECRET": client["client_secret"],
            "GMAIL_REFRESH_TOKEN": refresh,
            "EMAIL_FROM_EMAIL": args.sender,
            "EMAIL_PROVIDER": "gmail",
        }
        if not variables.get("EMAIL_TOKEN_ENCRYPTION_KEY"):
            updates["EMAIL_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
        for name, value in updates.items():
            subprocess.run(
                [railway, "variable", "set", *target, "--stdin", "--skip-deploys", name],
                input=value,
                text=True,
                capture_output=True,
                check=True,
            )
        print(
            "Gmail sender credentials saved privately in Railway. Redeploy food-ai-backend to activate them."
        )
        print(
            "No email was sent. After redeployment, run the documented delivery test or register a test account."
        )
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        httpx.HTTPError,
        subprocess.CalledProcessError,
    ) as error:
        # Do not print exception strings: provider errors may contain codes or credentials.
        print(
            f"Setup did not complete ({type(error).__name__}). Check Google consent, the Desktop client JSON and Railway access; then retry."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
