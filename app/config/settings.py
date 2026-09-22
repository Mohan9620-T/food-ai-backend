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
NVIDIA_CHAT_MAX_TOKENS = int(os.getenv("NVIDIA_CHAT_MAX_TOKENS", "4096"))
# Nemotron Super text chat can reason briefly without consuming its answer allowance.
NVIDIA_CHAT_REASONING_BUDGET = max(
    0, min(int(os.getenv("NVIDIA_CHAT_REASONING_BUDGET", "1024")), 8192)
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
NVIDIA_VISION_MAX_TOKENS = int(os.getenv("NVIDIA_VISION_MAX_TOKENS", "384"))
CHAT_VISION_OCR_ENABLED = os.getenv("CHAT_VISION_OCR_ENABLED", "false").lower() == "true"
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
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "5"))
RAG_SIMILARITY_THRESHOLD = float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.5"))
RAG_MAX_CONTEXT_CHUNKS = int(os.getenv("RAG_MAX_CONTEXT_CHUNKS", "6"))
RAG_MAX_CONTEXT_TOKENS = int(os.getenv("RAG_MAX_CONTEXT_TOKENS", "2000"))
RAG_CHUNK_CHARS = int(os.getenv("RAG_CHUNK_CHARS", "800"))
RAG_CHUNK_OVERLAP_CHARS = int(os.getenv("RAG_CHUNK_OVERLAP_CHARS", "100"))

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
