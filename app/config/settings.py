import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

load_dotenv()

logger = logging.getLogger(__name__)


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

# Production uses NVIDIA with Ollama fallback only when explicitly enabled. In development,
# LLM_PROVIDER=ollama keeps inference local-only and nvidia exercises failover.
APP_ENVIRONMENT = os.getenv("APP_ENVIRONMENT", "development").strip().lower()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
# Unset: local fallback is enabled in development and disabled in production.
# Set true only when a reachable Ollama server and the required models are provisioned.
ENABLE_OLLAMA_FALLBACK = (
    os.environ["ENABLE_OLLAMA_FALLBACK"].strip().lower() == "true"
    if os.getenv("ENABLE_OLLAMA_FALLBACK", "").strip()
    else None
)

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
# Used by the "reasoning"/"math"/"coding" chat profiles; falls back to
# NVIDIA_CHAT_MODEL when unset so a deployment with only one model configured
# still works unchanged.
NVIDIA_REASONING_MODEL = os.getenv("NVIDIA_REASONING_MODEL", "").strip()


def _parse_llm_profile_overrides() -> dict[str, dict]:
    raw = os.getenv("LLM_PROFILE_OVERRIDES", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("LLM_PROFILE_OVERRIDES must be a JSON object")
    except (json.JSONDecodeError, ValueError) as error:
        logger.warning(
            "settings.llm_profile_overrides_invalid", extra={"error_type": type(error).__name__}
        )
        return {}
    cleaned: dict[str, dict] = {}
    for name, override in parsed.items():
        if isinstance(name, str) and isinstance(override, dict):
            cleaned[name.strip().lower()] = override
    return cleaned


# A JSON object of {profile_name: {field: value}} tuning overrides, e.g.
# {"coding": {"temperature": 0.1}}. Invalid JSON, a non-object value, or a
# non-object per-profile override is logged and ignored rather than crashing
# the app; only a fixed field whitelist is honored (app/services/chat_profiles.py).
LLM_PROFILE_OVERRIDES = _parse_llm_profile_overrides()
# Deterministic, no-LLM-call pre-answer gate (app/services/turn_governance.py)
# that can block/escalate/clarify a turn before generation runs. Off by default.
ENABLE_TURN_GOVERNANCE = os.getenv("ENABLE_TURN_GOVERNANCE", "false").lower() == "true"
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

# Text-to-image uses a separate endpoint; the chat/vision models cannot render images.
ENABLE_IMAGE_GENERATION = os.getenv("ENABLE_IMAGE_GENERATION", "true").lower() == "true"
NVIDIA_IMAGE_MODEL = os.getenv("NVIDIA_IMAGE_MODEL", "black-forest-labs/flux.1-dev").strip()
NVIDIA_IMAGE_TIMEOUT_SECONDS = float(os.getenv("NVIDIA_IMAGE_TIMEOUT_SECONDS", "120"))
CHAT_VISION_OCR_ENABLED = os.getenv("CHAT_VISION_OCR_ENABLED", "false").lower() == "true"

# Ollama is a hard part of the default topology (local dev, Docker Compose) -
# /health/ready reports it and gates readiness on it. For an NVIDIA-only
# deployment that doesn't run Ollama at all (e.g. Railway without an Ollama
# service), set this to false: its status is still reported for visibility,
# it just no longer gates readiness. Fallback is controlled separately by
# ENABLE_OLLAMA_FALLBACK and is disabled by default in production.
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
# Global per-day cap on provider search calls (not direct link reads). Redis-backed;
# when Redis is unreachable the cap is not enforced - unmetered is better than unavailable.
WEB_SEARCH_DAILY_CAP = int(os.getenv("WEB_SEARCH_DAILY_CAP", "80"))
WEB_SEARCH_IMAGE_PREVIEW_TIMEOUT_SECONDS = float(
    os.getenv("WEB_SEARCH_IMAGE_PREVIEW_TIMEOUT_SECONDS", "6")
)
# Structured source persistence + status/sources stream events + preview images +
# the frontend source-card UI. Off: web search behaves exactly as it does today
# (markdown "### Sources" footer only). The budget cap and explicit-content filter
# above are hardening of search itself and always apply once ENABLE_WEB_SEARCH is on.
ENABLE_WEB_SEARCH_SOURCE_CARDS = (
    os.getenv("ENABLE_WEB_SEARCH_SOURCE_CARDS", "false").lower() == "true"
)
# Optional: enables reading private GitHub repositories/files via the API and raises
# the public rate limit. Public repos work without it. Never invented if unset - a
# private repo simply reports that authentication is required.
GITHUB_TOKEN = (_read_secret("GITHUB_TOKEN", "") or "").strip()
GITHUB_API_BASE_URL = os.getenv("GITHUB_API_BASE_URL", "https://api.github.com").rstrip("/")
# Bounded so a tree/directory link cannot pull an unbounded number of files.
GITHUB_TREE_MAX_FILES = int(os.getenv("GITHUB_TREE_MAX_FILES", "6"))

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

# Local development may use password login while email delivery is unavailable.
# This override has no effect outside development and is off unless explicitly set.
LOCAL_DISABLE_EMAIL_OTP = os.getenv("LOCAL_DISABLE_EMAIL_OTP", "false").lower() == "true"
ENABLE_EMAIL_OTP = os.getenv("ENABLE_EMAIL_OTP", "true").lower() == "true"
ENABLE_EMAIL_DELIVERY = os.getenv("ENABLE_EMAIL_DELIVERY", "true").lower() == "true"


def email_otp_enabled() -> bool:
    return (
        ENABLE_EMAIL_OTP
        and ENABLE_EMAIL_DELIVERY
        and not (APP_ENVIRONMENT == "development" and LOCAL_DISABLE_EMAIL_OTP)
    )


OTP_EXPIRY_MINUTES = int(os.getenv("OTP_EXPIRY_MINUTES", "10"))
OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
OTP_RATE_LIMIT = os.getenv("OTP_RATE_LIMIT", "10/minute")
OTP_RESEND_RATE_LIMIT = os.getenv("OTP_RESEND_RATE_LIMIT", "3/minute")

# Phase 4: documents. Existing inline binary storage is unchanged by default.
ENABLE_FILE_CLARIFY = os.getenv("ENABLE_FILE_CLARIFY", "true").lower() == "true"
ENABLE_TABLE_REVIEW = os.getenv("ENABLE_TABLE_REVIEW", "true").lower() == "true"
ENABLE_SANDBOX_EXECUTION = os.getenv("ENABLE_SANDBOX_EXECUTION", "false").lower() == "true"
SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "food-ai-sandbox:latest")
SANDBOX_TIMEOUT_SECONDS = int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "120"))
SANDBOX_MEMORY = os.getenv("SANDBOX_MEMORY", "2g")
SANDBOX_CPUS = os.getenv("SANDBOX_CPUS", "2")
SANDBOX_SEND_SAMPLES = os.getenv("SANDBOX_SEND_SAMPLES", "true").lower() == "true"
ENABLE_DOCUMENT_CITATIONS = os.getenv("ENABLE_DOCUMENT_CITATIONS", "false").lower() == "true"
DOCUMENT_GROUNDING_MIN_SCORE = float(os.getenv("DOCUMENT_GROUNDING_MIN_SCORE", "0.35"))
DOCUMENT_ATTRIBUTION_MIN_SUPPORT = float(os.getenv("DOCUMENT_ATTRIBUTION_MIN_SUPPORT", "0.45"))
ENABLE_LIBRARY = os.getenv("ENABLE_LIBRARY", "false").lower() == "true"
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "database")
STORAGE_LOCAL_DIR = os.getenv("STORAGE_LOCAL_DIR", "data/blobs")
STORAGE_SIGNED_URL_TTL_SECONDS = int(os.getenv("STORAGE_SIGNED_URL_TTL_SECONDS", "300"))
AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET", "")
AWS_REGION = os.getenv("AWS_REGION", "")
ENABLE_BACKGROUND_DOCUMENT_PROCESSING = (
    os.getenv("ENABLE_BACKGROUND_DOCUMENT_PROCESSING", "false").lower() == "true"
)

ENABLE_WORKSPACES = os.getenv("ENABLE_WORKSPACES", "false").lower() == "true"

# Phase 6 capabilities are opt-in; credentials remain environment-owned.
ENABLE_OAUTH_LOGIN = os.getenv("ENABLE_OAUTH_LOGIN", "false").lower() == "true"
ENABLE_API_KEYS = os.getenv("ENABLE_API_KEYS", "false").lower() == "true"
ENABLE_ROLES = os.getenv("ENABLE_ROLES", "false").lower() == "true"
ENABLE_LLM_GATEWAY = os.getenv("ENABLE_LLM_GATEWAY", "false").lower() == "true"
ENABLE_REQUEST_IDS = os.getenv("ENABLE_REQUEST_IDS", "false").lower() == "true"
OAUTH_FIREBASE_PROJECT_ID = os.getenv("OAUTH_FIREBASE_PROJECT_ID", "")
OAUTH_FIREBASE_API_KEY = os.getenv("OAUTH_FIREBASE_API_KEY", "")
OAUTH_FIREBASE_AUTH_DOMAIN = os.getenv("OAUTH_FIREBASE_AUTH_DOMAIN", "")
LLM_MAX_CONCURRENCY = max(1, int(os.getenv("LLM_MAX_CONCURRENCY", "8")))
LLM_MAX_RETRIES = max(0, int(os.getenv("LLM_MAX_RETRIES", "2")))
OTEL_ENABLED = os.getenv("OTEL_ENABLED", "false").lower() == "true"
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

# Phase 7: stateless invoice extraction. Reuses existing provider credentials.
ENABLE_INVOICE_EXTRACTION = os.getenv("ENABLE_INVOICE_EXTRACTION", "false").lower() == "true"
INVOICE_VLM_PROVIDER = os.getenv("INVOICE_VLM_PROVIDER", "ollama").strip().lower()
INVOICE_VLM_MODEL = os.getenv("INVOICE_VLM_MODEL", "").strip()
INVOICE_MAX_PAGES = max(1, min(10, int(os.getenv("INVOICE_MAX_PAGES", "5"))))
INVOICE_MAX_UPLOAD_BYTES = max(
    1, min(15 * 1024 * 1024, int(os.getenv("INVOICE_MAX_UPLOAD_BYTES", str(15 * 1024 * 1024))))
)
INVOICE_VLM_TIMEOUT_SECONDS = max(1, float(os.getenv("INVOICE_VLM_TIMEOUT_SECONDS", "120")))
INVOICE_EXTRACTION_RATE_LIMIT = os.getenv("INVOICE_EXTRACTION_RATE_LIMIT", "5/minute")

# Phase 8: explicitly provisioned, owner-scoped source repositories.
ENABLE_CODE_ASSISTANT = os.getenv("ENABLE_CODE_ASSISTANT", "false").lower() == "true"
CODE_REPOSITORY_ROOT = os.getenv("CODE_REPOSITORY_ROOT", "").strip()
CODE_MAX_FILES = max(1, min(500, int(os.getenv("CODE_MAX_FILES", "500"))))
CODE_AGENT_MAX_STEPS = max(1, min(8, int(os.getenv("CODE_AGENT_MAX_STEPS", "6"))))
CODE_AGENT_TIMEOUT_SECONDS = max(1, min(300, float(os.getenv("CODE_AGENT_TIMEOUT_SECONDS", "120"))))
