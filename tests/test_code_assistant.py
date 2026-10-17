"""Code assistant ownership, filesystem boundaries, Git and bounded fake-model loop."""

import asyncio
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.main import app
from app.models.user import User
from app.services import code_assistant as service
from app.services.chat_service import ChatService, _NvidiaFallbackError
from app.utils.security import create_access_token


class FakeModel:
    def __init__(self, actions):
        self.actions = list(actions)
        self.prompts = []

    async def decide(self, prompt):
        self.prompts.append(prompt)
        return json.dumps(self.actions.pop(0))


@pytest.fixture
def code(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_CODE_ASSISTANT", True)
    monkeypatch.setattr(settings, "CODE_REPOSITORY_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "CODE_MAX_FILES", 500)
    monkeypatch.setattr(settings, "CODE_AGENT_MAX_STEPS", 6)
    monkeypatch.setattr(settings, "CODE_AGENT_TIMEOUT_SECONDS", 120)
    root = tmp_path / "401" / "project"
    root.mkdir(parents=True)
    (root / "main.py").write_text("def greet():\n    return 'Hello'\n", encoding="utf-8")
    db_session.add(User(id=401, fullname="Code owner", email="code@example.com", password="unused"))
    db_session.add(
        User(id=402, fullname="Other owner", email="other-code@example.com", password="unused")
    )
    db_session.commit()
    headers = {"Authorization": "Bearer " + create_access_token({"sub": "401", "auth_version": 0})}
    response = client.post(
        "/code/repositories", json={"name": "Project", "path": "project"}, headers=headers
    )
    assert response.status_code == 201, response.text
    url = "/code/repositories/" + str(response.json()["id"])
    return client, headers, root, url


def test_index_search_files_disconnect_and_snapshot(code):
    client, headers, root, url = code
    assert (
        client.post(url + "/agent", headers=headers, json={"question": "Explain"}).status_code
        == 409
    )
    response = client.post(url + "/index", headers=headers)
    assert response.json()["file_count"] == 1
    assert response.json()["indexed_at"]
    (root / "main.py").write_text("changed", encoding="utf-8")
    assert client.get(url + "/files", headers=headers).json() == ["main.py"]
    assert (
        "greet"
        in client.get(url + "/file", params={"path": "main.py"}, headers=headers).json()["text"]
    )
    hits = client.get(url + "/search", params={"q": "greet"}, headers=headers).json()
    assert hits[0]["line"] == 1 and hits[0]["path"] == "main.py"
    assert client.get(url + "/file", params={"path": "../.env"}, headers=headers).status_code == 404
    assert client.post(url + "/index", headers=headers).status_code == 200
    assert client.get(url + "/search", params={"q": "greet"}, headers=headers).json() == []
    assert client.delete(url, headers=headers).status_code == 204
    assert root.is_dir()
    assert client.get("/code/repositories", headers=headers).json() == []


@pytest.mark.parametrize(
    "suffix,method",
    [
        ("/index", "post"),
        ("/files", "get"),
        ("/file?path=main.py", "get"),
        ("/search?q=code", "get"),
        ("/git/status", "get"),
        ("/agent", "post"),
        ("", "delete"),
    ],
)
def test_cross_owner_hidden(code, suffix, method):
    client, _, _, url = code
    headers = {"Authorization": "Bearer " + create_access_token({"sub": "402", "auth_version": 0})}
    kwargs = {"json": {"question": "Explain"}} if suffix == "/agent" else {}
    assert getattr(client, method)(url + suffix, headers=headers, **kwargs).status_code == 404


def test_auth_flag_duplicate_missing_root(code, monkeypatch):
    client, headers, _, _ = code
    assert client.get("/code/repositories").status_code == 401
    assert (
        client.post(
            "/code/repositories", headers=headers, json={"name": "Again", "path": "project"}
        ).status_code
        == 409
    )
    assert client.get("/code/repositories", headers=headers).json()[0]["name"] == "Project"
    assert client.get("/library/capabilities", headers=headers).json()["code_assistant"] is True
    monkeypatch.setattr(settings, "CODE_REPOSITORY_ROOT", "")
    assert (
        client.post(
            "/code/repositories", headers=headers, json={"name": "New", "path": "other"}
        ).status_code
        == 503
    )
    monkeypatch.setattr(settings, "ENABLE_CODE_ASSISTANT", False)
    assert client.get("/code/repositories", headers=headers).status_code == 404


@pytest.mark.parametrize(
    "path", ["../402/project", "/etc", "C:/private", "project\\..\\secret", ".", "missing"]
)
def test_invalid_connection_path(code, path):
    client, headers, _, _ = code
    assert client.post(
        "/code/repositories", headers=headers, json={"name": "Bad", "path": path}
    ).status_code in (400, 404)


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        "secrets/key.py",
        "node_modules/a.js",
        "config/credentials.json",
        "auth_token.py",
        "image.png",
        "private-key.txt",
    ],
)
def test_secret_and_binary_names_excluded(name):
    from pathlib import Path

    assert not service.allowed(Path(name))


def test_scanner_omissions_and_limit_preserve_old_snapshot(code, monkeypatch):
    client, headers, root, url = code
    (root / "binary.txt").write_bytes(b"hello\0there")
    (root / "invalid.txt").write_bytes(b"\xff\xfe")
    (root / "large.txt").write_bytes(b"x" * 200001)
    (root / ".env").write_text("SECRET", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "a.js").write_text("ignored", encoding="utf-8")
    assert client.post(url + "/index", headers=headers).json()["file_count"] == 1
    (root / "other.py").write_text("other", encoding="utf-8")
    monkeypatch.setattr(settings, "CODE_MAX_FILES", 1)
    assert client.post(url + "/index", headers=headers).status_code == 413
    assert client.get(url + "/files", headers=headers).json() == ["main.py"]


def test_search_rank_empty_and_bound():
    assert (
        service.search({"a.py": "hello world\nhello", "b.py": "hello"}, "hello world")[0]["score"]
        == 2
    )
    assert service.search({"a.py": "hello"}, "!!!") == []
    assert len(service.search({"a.py": "hello\n" * 100}, "hello")) == 30


def test_real_git_views_do_not_modify_worktree(code):
    client, headers, root, url = code

    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init")
    git("add", "main.py")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "-c",
        "core.hooksPath=NUL",
        "commit",
        "-m",
        "Initial",
    )
    assert "Index" in client.get(url + "/git/diff", headers=headers).json()["text"]
    client.post(url + "/index", headers=headers)
    (root / "main.py").write_text("def greet():\n    return 'Changed'\n", encoding="utf-8")
    (root / ".env").write_text("PRIVATE_KEY=do-not-expose", encoding="utf-8")
    for view in ("status", "log", "branches", "diff"):
        response = client.get(url + "/git/" + view, headers=headers)
        assert response.status_code == 200, response.text
        assert "do-not-expose" not in response.text
    assert "Changed" in client.get(url + "/git/diff", headers=headers).json()["text"]
    assert client.get(url + "/git/push", headers=headers).status_code == 400
    assert "Changed" in (root / "main.py").read_text(encoding="utf-8")


def test_git_missing_and_output_cap(code, monkeypatch):
    client, headers, _, url = code
    assert client.get(url + "/git/status", headers=headers).status_code == 409

    def fake_run(*args, **kwargs):
        assert isinstance(args[0], list) and "shell" not in kwargs
        assert kwargs["env"]["GIT_OPTIONAL_LOCKS"] == "0"
        kwargs["stdout"].write(b"x" * 60002)

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    text = client.get(url + "/git/status", headers=headers).json()["text"]
    assert text.endswith("[Output truncated]") and len(text) < 60100


def test_agent_loop_provenance_and_no_writes(code):
    client, headers, root, url = code
    client.post(url + "/index", headers=headers)
    fake = FakeModel(
        [
            {"tool": "list_files"},
            {"tool": "search_code", "argument": "greet"},
            {"tool": "read_file", "argument": "main.py"},
            {"tool": "read_file", "argument": "../.env"},
            {"tool": "finish", "answer": "main.py:1 defines greet."},
        ]
    )
    app.dependency_overrides[service.get_agent_model] = lambda: fake
    response = client.post(url + "/agent", headers=headers, json={"question": "Explain greet"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "complete" and len(result["trace"]) == 4
    assert "1: def greet" in result["trace"][2]["result"]
    assert "not found" in result["trace"][3]["result"]
    assert "Untrusted tool result" in fake.prompts[-1]
    assert "Hello" in (root / "main.py").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "action",
    [
        {"tool": "run_command", "argument": "rm -rf /"},
        {"tool": "finish", "answer": ""},
        {"tool": "read_file", "extra": "invalid"},
    ],
)
def test_agent_invalid_model_response(code, action):
    client, headers, _, url = code
    client.post(url + "/index", headers=headers)
    app.dependency_overrides[service.get_agent_model] = lambda: FakeModel([action])
    response = client.post(url + "/agent", headers=headers, json={"question": "Explain"})
    assert response.status_code == 502
    assert "rm -rf" not in response.text


def test_agent_step_limit_and_git_tools(code, monkeypatch):
    client, headers, _, url = code
    client.post(url + "/index", headers=headers)
    monkeypatch.setattr(settings, "CODE_AGENT_MAX_STEPS", 2)
    monkeypatch.setattr(service, "git_view", lambda row, view: "safe " + view)
    app.dependency_overrides[service.get_agent_model] = lambda: FakeModel(
        [{"tool": "git_status"}, {"tool": "git_diff"}]
    )
    result = client.post(url + "/agent", headers=headers, json={"question": "Review"}).json()
    assert result["status"] == "step_limit"
    assert result["trace"][1]["result"] == "safe diff"


def test_agent_timeout_and_unavailable(code, monkeypatch):
    client, headers, _, url = code
    client.post(url + "/index", headers=headers)

    async def stall(prompt):
        await asyncio.sleep(1)

    monkeypatch.setattr(settings, "CODE_AGENT_TIMEOUT_SECONDS", 0.01)
    app.dependency_overrides[service.get_agent_model] = lambda: SimpleNamespace(decide=stall)
    assert (
        client.post(url + "/agent", headers=headers, json={"question": "Explain"}).status_code
        == 504
    )


def test_agent_provider_uses_existing_transport(monkeypatch):
    monkeypatch.setattr(ChatService, "_use_nvidia_primary", lambda self: False)
    complete = AsyncMock(return_value='{"tool":"finish","answer":"done"}')
    monkeypatch.setattr(ChatService, "_complete_with_ollama", complete)
    assert "done" in asyncio.run(service.AgentModel().decide("Inspect"))

    body = complete.call_args.args[0]
    assert body["messages"] == [{"role": "user", "content": "Inspect"}]
    monkeypatch.setattr(ChatService, "_use_nvidia_primary", lambda self: True)
    primary = AsyncMock(return_value="primary")
    monkeypatch.setattr(ChatService, "_complete_with_nvidia", primary)
    assert asyncio.run(service.AgentModel().decide("Inspect")) == "primary"
    primary.side_effect = _NvidiaFallbackError("overloaded")
    monkeypatch.setattr(ChatService, "_require_ollama_fallback", lambda self, error: None)
    assert "done" in asyncio.run(service.AgentModel().decide("Inspect"))


def test_rate_limit_and_private_cache(code):
    client, headers, _, url = code
    response = client.get(url + "/files", headers=headers)
    assert response.headers["cache-control"] == "private, no-store"
    for _ in range(5):
        assert client.post(url + "/index", headers=headers).status_code == 200
    assert client.post(url + "/index", headers=headers).status_code == 429


def test_api_key_ownership_and_revocation(code, db_session, monkeypatch):
    from datetime import datetime, timezone

    from app.repositories.api_key_repository import ApiKeyRepository

    client, _, _, url = code
    monkeypatch.setattr(settings, "ENABLE_API_KEYS", True)
    row, raw = ApiKeyRepository().create(db_session, 401, "Code integration")
    assert client.get(url + "/files", headers={"X-API-Key": raw}).status_code == 200
    _, other = ApiKeyRepository().create(db_session, 402, "Other integration")
    assert client.get(url + "/files", headers={"X-API-Key": other}).status_code == 404
    row.revoked_at = datetime.now(timezone.utc)
    db_session.commit()
    assert client.get(url + "/files", headers={"X-API-Key": raw}).status_code == 401


def test_scan_io_error_and_junction_root(code, monkeypatch):
    from pathlib import Path

    client, headers, _, url = code

    def fail(root):
        raise OSError("sensitive server path")

    monkeypatch.setattr(service, "scan", fail)
    response = client.post(url + "/index", headers=headers)
    assert response.status_code == 409 and "sensitive" not in response.text
    monkeypatch.setattr(Path, "is_junction", lambda self: self.name == "401")
    response = client.post(url + "/index", headers=headers)
    assert response.status_code == 400
