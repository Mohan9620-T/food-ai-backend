import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

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


def _database_setting(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value and value.lower() not in {"none", "null"}:
            return value
    return None


def _database_url() -> str:
    """Accept managed Postgres URLs without leaking credentials in setup errors."""
    configured_url = _database_setting("DATABASE_URL")
    if configured_url:
        if "${{" in configured_url:
            raise ValueError("DATABASE_URL contains an unresolved Railway service reference.")
        if configured_url.startswith("postgres://"):
            configured_url = "postgresql://" + configured_url[len("postgres://") :]
        try:
            parsed = make_url(configured_url)
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError("invalid port")
        except (ArgumentError, ValueError):
            raise ValueError(
                "DATABASE_URL is invalid. Set it to the Postgres service's DATABASE_URL "
                "reference in Railway; do not use a URL containing a missing/None port."
            ) from None
        return parsed.render_as_string(hide_password=False)

    host = _database_setting("DB_HOST", "PGHOST")
    name = _database_setting("DB_NAME", "PGDATABASE")
    user = _database_setting("DB_USER", "PGUSER")
    if not all((host, name, user)):
        raise ValueError(
            "Database configuration is missing. Set DATABASE_URL to the Railway Postgres "
            "service reference, or provide DB_HOST, DB_NAME and DB_USER (or PGHOST, "
            "PGDATABASE and PGUSER)."
        )
    raw_port = _database_setting("DB_PORT", "PGPORT") or "5432"
    try:
        port = int(raw_port)
        if not 1 <= port <= 65535:
            raise ValueError("invalid port")
    except ValueError:
        raise ValueError("DB_PORT/PGPORT must be a port number between 1 and 65535.") from None
    password = _read_secret("DB_PASSWORD")
    if password is None:
        password = _read_secret("PGPASSWORD", "")
    return URL.create(
        "postgresql", username=user, password=password, host=host, port=port, database=name
    ).render_as_string(hide_password=False)


DB_HOST = _database_setting("DB_HOST", "PGHOST")
DB_PORT = _database_setting("DB_PORT", "PGPORT") or "5432"
DB_NAME = _database_setting("DB_NAME", "PGDATABASE")
DB_USER = _database_setting("DB_USER", "PGUSER")
DB_PASSWORD = _read_secret("DB_PASSWORD")
DATABASE_URL = _database_url()

EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "auto").strip().lower() or "auto"
EMAIL_FROM_EMAIL = os.getenv("EMAIL_FROM_EMAIL", "").strip()
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Food AI Assistant").strip()
PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", "").strip().rstrip("/")
EMAIL_TIMEOUT_SECONDS = 15
EMAIL_TOKEN_ENCRYPTION_KEY = _read_secret("EMAIL_TOKEN_ENCRYPTION_KEY", "") or ""
RESEND_API_KEY = _read_secret("RESEND_API_KEY", "") or ""
GMAIL_CLIENT_ID = os.getenv("GMAIL_CLIENT_ID", "").strip()
GMAIL_CLIENT_SECRET = _read_secret("GMAIL_CLIENT_SECRET", "") or ""
GMAIL_REFRESH_TOKEN = _read_secret("GMAIL_REFRESH_TOKEN", "") or ""
MICROSOFT_CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
MICROSOFT_CLIENT_SECRET = _read_secret("MICROSOFT_CLIENT_SECRET", "") or ""
MICROSOFT_TENANT_ID = os.getenv("MICROSOFT_TENANT_ID", "common").strip() or "common"
MICROSOFT_REFRESH_TOKEN = _read_secret("MICROSOFT_REFRESH_TOKEN", "") or ""

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
try:
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587").strip() or "587")
except ValueError:
    # Optional mail configuration must not prevent the application from starting.
    SMTP_PORT = 0
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = _read_secret("SMTP_PASSWORD", "") or ""
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "").strip() or SMTP_USERNAME
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() == "true"

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "300"))
OLLAMA_CONNECT_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_CONNECT_TIMEOUT_SECONDS", "5"))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "1h").strip() or "1h"
OLLAMA_CHAT_THINK = os.getenv("OLLAMA_CHAT_THINK", "false").lower() == "true"
OLLAMA_CHAT_MAX_TOKENS = int(os.getenv("OLLAMA_CHAT_MAX_TOKENS", "2048"))
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "qwen3-vl:4b")
OLLAMA_VISION_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_VISION_TIMEOUT_SECONDS", "660"))
OLLAMA_VISION_MAX_DIMENSION = int(os.getenv("OLLAMA_VISION_MAX_DIMENSION", "1024"))
OLLAMA_CHAT_VISION_MODEL = os.getenv("OLLAMA_CHAT_VISION_MODEL", "qwen3-vl:4b").strip()
OLLAMA_CHAT_VISION_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_CHAT_VISION_TIMEOUT_SECONDS", "660"))

# Production always uses NVIDIA first with one Ollama fallback. In development,
# LLM_PROVIDER=ollama keeps inference local-only and nvidia exercises failover.
APP_ENVIRONMENT = os.getenv("APP_ENVIRONMENT", "development").strip().lower()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()

NVIDIA_API_KEY = (_read_secret("NVIDIA_API_KEY", "") or "").strip()
NVIDIA_API_BASE_URL = os.getenv(
    "NVIDIA_API_BASE_URL", "https://integrate.api.nvidia.com/v1"
).rstrip("/")
NVIDIA_CHAT_MODEL = os.getenv("NVIDIA_CHAT_MODEL", "nvidia/nemotron-3-super-120b-a12b").strip()
NVIDIA_CHAT_CONNECT_TIMEOUT_SECONDS = float(os.getenv("NVIDIA_CHAT_CONNECT_TIMEOUT_SECONDS", "5"))
NVIDIA_CHAT_TIMEOUT_SECONDS = float(os.getenv("NVIDIA_CHAT_TIMEOUT_SECONDS", "30"))
NVIDIA_CHAT_COMPLETE_TIMEOUT_SECONDS = float(
    os.getenv("NVIDIA_CHAT_COMPLETE_TIMEOUT_SECONDS", "120")
)
NVIDIA_CHAT_MAX_TOKENS = int(os.getenv("NVIDIA_CHAT_MAX_TOKENS", "4096"))
# Disable hidden reasoning by default so capped answers retain their visible-output budget.
NVIDIA_CHAT_REASONING_BUDGET = max(
    0, min(int(os.getenv("NVIDIA_CHAT_REASONING_BUDGET", "0")), 8192)
)
# Additional same-provider requests for a text answer stopped by its token limit.
CHAT_MAX_CONTINUATIONS = max(0, min(int(os.getenv("CHAT_MAX_CONTINUATIONS", "3")), 8))
# Conversational chat sampling. Kept separate from DOCUMENT_AI_TEMPERATURE so
# document generation/analysis (structured, low-variance) is never coupled to
# conversational tone tuning.
CHAT_TEMPERATURE = float(os.getenv("CHAT_TEMPERATURE", "0.3"))
DOCUMENT_AI_TEMPERATURE = float(os.getenv("DOCUMENT_AI_TEMPERATURE", "0.2"))
NVIDIA_CHAT_TOP_P = float(os.getenv("NVIDIA_CHAT_TOP_P", "0.9"))
# Rough, provider-agnostic token estimate (characters // divisor) used only to
# budget how much history fits in a request - not an exact tokenizer count.
# SYSTEM_PROMPT alone is already ~4200 estimated tokens, so this must stay well
# above that plus a full 24-message window before it starts trimming, or it
# would override the message-count window on nearly every turn.
CONTEXT_TOKEN_CHAR_DIVISOR = int(os.getenv("CONTEXT_TOKEN_CHAR_DIVISOR", "4"))
CONTEXT_TOKEN_BUDGET = int(os.getenv("CONTEXT_TOKEN_BUDGET", "24000"))
# Total AI time for a document, including primary/fallback and streamed tokens.
DOCUMENT_AI_TIMEOUT_SECONDS = float(os.getenv("DOCUMENT_AI_TIMEOUT_SECONDS", "90"))
DOCUMENT_AI_MAX_TOKENS = int(os.getenv("DOCUMENT_AI_MAX_TOKENS", "2048"))
DOCUMENT_GENERATION_MAX_TOKENS = int(os.getenv("DOCUMENT_GENERATION_MAX_TOKENS", "8192"))
DOCUMENT_PLAN_CONFIDENCE_THRESHOLD = float(os.getenv("DOCUMENT_PLAN_CONFIDENCE_THRESHOLD", "0.65"))
DOCUMENT_PIPELINE_MAX_STEPS = max(1, min(int(os.getenv("DOCUMENT_PIPELINE_MAX_STEPS", "8")), 20))
# OOXML (DOCX/XLSX/PPTX) archive limits are checked before any XML parser runs.
DOCUMENT_OOXML_MAX_UNCOMPRESSED_BYTES = int(
    os.getenv("DOCUMENT_OOXML_MAX_UNCOMPRESSED_BYTES", str(128 * 1024 * 1024))
)
DOCUMENT_OOXML_MAX_TOTAL_RATIO = int(os.getenv("DOCUMENT_OOXML_MAX_TOTAL_RATIO", "100"))
DOCUMENT_OOXML_MAX_ENTRY_RATIO = int(os.getenv("DOCUMENT_OOXML_MAX_ENTRY_RATIO", "200"))
DOCUMENT_OOXML_MAX_ENTRIES = int(os.getenv("DOCUMENT_OOXML_MAX_ENTRIES", "5000"))
DOCUMENT_CONVERSION_TIMEOUT_SECONDS = int(os.getenv("DOCUMENT_CONVERSION_TIMEOUT_SECONDS", "90"))
# Optional explicit path to soffice. When empty, common install paths and PATH are checked.
LIBREOFFICE_BINARY = os.getenv("LIBREOFFICE_BINARY", "").strip()
TESSERACT_BINARY = os.getenv("TESSERACT_BINARY", "").strip()
NVIDIA_TEST_CHAT_MODEL = os.getenv(
    "NVIDIA_TEST_CHAT_MODEL", "nvidia/nemotron-3-ultra-550b-a55b"
).strip()
NVIDIA_TEST_CHAT_TIMEOUT_SECONDS = float(os.getenv("NVIDIA_TEST_CHAT_TIMEOUT_SECONDS", "60"))
NVIDIA_CHAT_VISION_MODEL = os.getenv(
    "NVIDIA_CHAT_VISION_MODEL", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
).strip()
NVIDIA_VISION_CONNECT_TIMEOUT_SECONDS = float(
    os.getenv("NVIDIA_VISION_CONNECT_TIMEOUT_SECONDS", "5")
)
NVIDIA_VISION_TIMEOUT_SECONDS = float(os.getenv("NVIDIA_VISION_TIMEOUT_SECONDS", "45"))
NVIDIA_VISION_MAX_DIMENSION = int(os.getenv("NVIDIA_VISION_MAX_DIMENSION", "768"))
# 384 was tuned for short answers (e.g. "what's on this plate?") but silently
# truncated any request that asks the assistant to act on image data - e.g. a
# diet/workout plan derived from a body-composition scan. This is the chat
# vision path only (ChatVisionService); meal-photo logging uses a separate,
# unaffected code path, so raising this doesn't change meal-logging cost/latency.
NVIDIA_VISION_MAX_TOKENS = int(os.getenv("NVIDIA_VISION_MAX_TOKENS", "2048"))
CHAT_VISION_OCR_ENABLED = os.getenv("CHAT_VISION_OCR_ENABLED", "false").lower() == "true"

# Ollama is a hard part of the default topology (local dev, Docker Compose) -
# /health/ready reports it and gates readiness on it. For an NVIDIA-only
# deployment that doesn't run Ollama at all (e.g. Railway without an Ollama
# service), set this to false: its status is still reported for visibility,
# it just no longer gates readiness. The NVIDIA->Ollama chat/vision failover
# code itself is unaffected either way - it simply fails cleanly per-request
# if Ollama is unreachable.
OLLAMA_REQUIRED_FOR_READINESS = os.getenv("OLLAMA_REQUIRED_FOR_READINESS", "true").lower() == "true"
USDA_API_KEY = os.getenv("USDA_API_KEY", "").strip()
USDA_API_URL = os.getenv("USDA_API_URL", "https://api.nal.usda.gov/fdc/v1").rstrip("/")
USDA_TIMEOUT_SECONDS = int(os.getenv("USDA_TIMEOUT_SECONDS", "15"))

# Redis is a fast, optional cache for recently-assembled chat context. Postgres
# remains the permanent source of truth; every cache read/write degrades to a
# no-op (falling back to Postgres) when Redis is disabled or unreachable.
ENABLE_REDIS_CACHE = os.getenv("ENABLE_REDIS_CACHE", "false").lower() == "true"
REDIS_URL = os.getenv("REDIS_URL", "")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = _read_secret("REDIS_PASSWORD", "")
REDIS_TIMEOUT_SECONDS = float(os.getenv("REDIS_TIMEOUT_SECONDS", "2"))
REDIS_CHAT_TTL_SECONDS = int(os.getenv("REDIS_CHAT_TTL_SECONDS", "3600"))

# ChromaDB semantic document retrieval - additive to (never a replacement for)
# the deterministic raw_text extraction/injection path. Fully inert when the
# flag is off, and falls back to that deterministic path when Chroma or the
# embedding provider is unreachable.
ENABLE_SEMANTIC_RAG = os.getenv("ENABLE_SEMANTIC_RAG", "false").lower() == "true"
CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
CHROMA_COLLECTION_PREFIX = os.getenv("CHROMA_COLLECTION_PREFIX", "foodai")
CHROMA_TIMEOUT_SECONDS = float(os.getenv("CHROMA_TIMEOUT_SECONDS", "10"))
# "nvidia" tries NVIDIA embeddings with an Ollama fallback; "ollama" stays fully local.
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "nvidia").strip().lower()
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nvidia/nemotron-3-embed-1b").strip()
OLLAMA_EMBEDDING_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text").strip()
# 0.5 looked like a reasonable default on paper but was measured live against
# nvidia/nemotron-3-embed-1b (M5 verification) and rejected true positives: a
# near-verbatim matching chunk scored only ~0.47, while unrelated queries
# scored ~0.01-0.05. 0.2 keeps a wide margin below real matches (~0.30-0.47)
# and well above noise (~0.05) for this model; recalibrate if the embedding
# model changes.
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "5"))
RAG_SIMILARITY_THRESHOLD = float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.2"))
RAG_MAX_CONTEXT_CHUNKS = int(os.getenv("RAG_MAX_CONTEXT_CHUNKS", "6"))
RAG_MAX_CONTEXT_TOKENS = int(os.getenv("RAG_MAX_CONTEXT_TOKENS", "2000"))
RAG_CHUNK_CHARS = int(os.getenv("RAG_CHUNK_CHARS", "800"))
RAG_CHUNK_OVERLAP_CHARS = int(os.getenv("RAG_CHUNK_OVERLAP_CHARS", "100"))

# Automatic factual lookup with Tavily and a key-free Wikipedia fallback.
# Failed lookups report unavailable evidence instead of guessing current facts.
ENABLE_WEB_SEARCH = os.getenv("ENABLE_WEB_SEARCH", "false").lower() == "true"
WIKIPEDIA_SEARCH_ENABLED = os.getenv("WIKIPEDIA_SEARCH_ENABLED", "true").lower() == "true"
TAVILY_API_KEY = (_read_secret("TAVILY_API_KEY", "") or "").strip()
TAVILY_API_BASE_URL = os.getenv("TAVILY_API_BASE_URL", "https://api.tavily.com").rstrip("/")
TAVILY_TIMEOUT_SECONDS = float(os.getenv("TAVILY_TIMEOUT_SECONDS", "10"))
WEB_SEARCH_MAX_RESULTS = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5"))
WEB_FETCH_TIMEOUT_SECONDS = float(os.getenv("WEB_FETCH_TIMEOUT_SECONDS", "12"))

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:4200").split(",")
    if origin.strip()
]


def local_development_origin_regex() -> str | None:
    """Allow changing local dev-server ports without widening deployed CORS rules."""
    if APP_ENVIRONMENT != "development":
        return None
    return r"https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?"


JWT_SECRET_KEY = _read_secret("JWT_SECRET_KEY", "dev-only-change-this-secret") or ""
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
REMEMBERED_ACCESS_TOKEN_EXPIRE_DAYS = int(os.getenv("REMEMBERED_ACCESS_TOKEN_EXPIRE_DAYS", "30"))

LOGIN_RATE_LIMIT = os.getenv("LOGIN_RATE_LIMIT", "5/minute")
REGISTER_RATE_LIMIT = os.getenv("REGISTER_RATE_LIMIT", "5/minute")
MEAL_CREATE_RATE_LIMIT = os.getenv("MEAL_CREATE_RATE_LIMIT", "10/minute")
CHAT_VISION_RATE_LIMIT = os.getenv("CHAT_VISION_RATE_LIMIT", "2/minute")
MIGRATION_CHECK_ENABLED = os.getenv("MIGRATION_CHECK_ENABLED", "true").lower() == "true"
