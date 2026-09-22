from unittest.mock import patch

import pytest

from app.config import settings


@pytest.fixture
def database_environment(monkeypatch):
    for name in (
        "DATABASE_URL",
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "DB_PASSWORD_FILE",
        "PGHOST",
        "PGPORT",
        "PGDATABASE",
        "PGUSER",
        "PGPASSWORD",
        "PGPASSWORD_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_railway_postgres_url_is_used_instead_of_split_settings(database_environment):
    database_environment.setenv(
        "DATABASE_URL", "postgres://user:secret@postgres.railway.internal:5432/railway"
    )
    database_environment.setenv("DB_PORT", "None")
    assert (
        settings._database_url()
        == "postgresql://user:secret@postgres.railway.internal:5432/railway"
    )


@pytest.mark.parametrize("port", ["", "None", "null", "5432"])
def test_missing_postgres_port_uses_default(database_environment, port):
    database_environment.setenv("PGHOST", "postgres.railway.internal")
    database_environment.setenv("PGDATABASE", "railway")
    database_environment.setenv("PGUSER", "postgres")
    database_environment.setenv("PGPORT", port)
    assert ":5432/railway" in settings._database_url()


def test_split_database_password_roundtrips_reserved_characters(database_environment):
    from sqlalchemy.engine import make_url

    database_environment.setenv("DB_HOST", "localhost")
    database_environment.setenv("DB_NAME", "food")
    database_environment.setenv("DB_USER", "user@example")
    database_environment.setenv("DB_PASSWORD", "space + colon:@/percent%")
    parsed = make_url(settings._database_url())
    assert parsed.username == "user@example"
    assert parsed.password == "space + colon:@/percent%"


def test_missing_database_config_has_actionable_error(database_environment):
    with pytest.raises(ValueError, match="Set DATABASE_URL"):
        settings._database_url()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:PRIVATE@host:None/db",
        "postgresql://user:PRIVATE@host:99999/db",
        "${{Postgres.DATABASE_URL}}",
    ],
)
def test_bad_database_url_does_not_expose_password(database_environment, url):
    database_environment.setenv("DATABASE_URL", url)
    with pytest.raises(ValueError, match="DATABASE_URL") as error:
        settings._database_url()
    assert "PRIVATE" not in str(error.value)


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
