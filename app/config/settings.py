import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()


def _read_secret(name: str, default: str | None = None) -> str | None:
    """Read a setting directly or from its Docker/Kubernetes-style file mount."""
    value = os.getenv(name)
    if value is not None:
        return value

    secret_path = os.getenv(f"{name}_FILE")
    if not secret_path:
        return default

    secret = Path(secret_path).read_text(encoding="utf-8").strip()
    if not secret:
        raise ValueError(f"{name}_FILE points to an empty secret file")
    return secret


DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = _read_secret("DB_PASSWORD")
DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql://{DB_USER}:{quote_plus(DB_PASSWORD or '')}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USERNAME or "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "300"))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "1h").strip() or "1h"
OLLAMA_CHAT_THINK = os.getenv("OLLAMA_CHAT_THINK", "false").lower() == "true"
OLLAMA_CHAT_MAX_TOKENS = int(os.getenv("OLLAMA_CHAT_MAX_TOKENS", "768"))
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "qwen3-vl:4b")
OLLAMA_VISION_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_VISION_TIMEOUT_SECONDS", "660"))
OLLAMA_VISION_MAX_DIMENSION = int(os.getenv("OLLAMA_VISION_MAX_DIMENSION", "1024"))
OLLAMA_CHAT_VISION_MODEL = os.getenv("OLLAMA_CHAT_VISION_MODEL", "qwen3-vl:4b").strip()
OLLAMA_CHAT_VISION_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_CHAT_VISION_TIMEOUT_SECONDS", "660"))
USDA_API_KEY = os.getenv("USDA_API_KEY", "").strip()
USDA_API_URL = os.getenv("USDA_API_URL", "https://api.nal.usda.gov/fdc/v1").rstrip("/")
USDA_TIMEOUT_SECONDS = int(os.getenv("USDA_TIMEOUT_SECONDS", "15"))

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:4200").split(",")
    if origin.strip()
]

JWT_SECRET_KEY = _read_secret("JWT_SECRET_KEY", "dev-only-change-this-secret") or ""
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
REMEMBERED_ACCESS_TOKEN_EXPIRE_DAYS = int(os.getenv("REMEMBERED_ACCESS_TOKEN_EXPIRE_DAYS", "30"))

LOGIN_RATE_LIMIT = os.getenv("LOGIN_RATE_LIMIT", "5/minute")
REGISTER_RATE_LIMIT = os.getenv("REGISTER_RATE_LIMIT", "5/minute")
MEAL_CREATE_RATE_LIMIT = os.getenv("MEAL_CREATE_RATE_LIMIT", "10/minute")
CHAT_VISION_RATE_LIMIT = os.getenv("CHAT_VISION_RATE_LIMIT", "2/minute")
MIGRATION_CHECK_ENABLED = os.getenv("MIGRATION_CHECK_ENABLED", "true").lower() == "true"
