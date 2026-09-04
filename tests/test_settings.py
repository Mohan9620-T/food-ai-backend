from unittest.mock import patch

import pytest

from app.config import settings


def test_read_secret_prefers_direct_environment_value(monkeypatch):
    monkeypatch.setenv("TEST_SECRET", "environment-value")
    monkeypatch.setenv("TEST_SECRET_FILE", "/run/secrets/test_secret")

    with patch("app.config.settings.Path.read_text") as read_text:
        assert settings._read_secret("TEST_SECRET") == "environment-value"
        read_text.assert_not_called()


def test_read_secret_uses_file_mount(monkeypatch):
    monkeypatch.delenv("TEST_SECRET", raising=False)
    monkeypatch.setenv("TEST_SECRET_FILE", "/run/secrets/test_secret")

    with patch("app.config.settings.Path.read_text", return_value="file-value\n"):
        assert settings._read_secret("TEST_SECRET") == "file-value"


def test_read_secret_rejects_empty_file(monkeypatch):
    monkeypatch.delenv("TEST_SECRET", raising=False)
    monkeypatch.setenv("TEST_SECRET_FILE", "/run/secrets/test_secret")

    with (
        patch("app.config.settings.Path.read_text", return_value="\n"),
        pytest.raises(ValueError, match="empty secret file"),
    ):
        settings._read_secret("TEST_SECRET")


def test_read_secret_returns_default_without_environment_or_file(monkeypatch):
    monkeypatch.delenv("TEST_SECRET", raising=False)
    monkeypatch.delenv("TEST_SECRET_FILE", raising=False)

    assert settings._read_secret("TEST_SECRET", "fallback") == "fallback"
