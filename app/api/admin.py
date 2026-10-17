from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database.chroma_client import get_chroma_client
from app.database.database import get_db
from app.database.redis_client import get_redis_client
from app.services.chat_profiles import resolve_profile
from app.storage import backend
from app.utils.auth_dependency import require_role


def enabled():
    if not settings.ENABLE_ROLES:
        raise HTTPException(404, "Administration is not enabled")


router = APIRouter(
    prefix="/admin",
    tags=["Administration"],
    dependencies=[Depends(enabled), Depends(require_role("admin"))],
)


@router.get("/models")
def models():
    return {
        "profiles": [
            {"name": name, "model": resolve_profile(name).model}
            for name in ("general", "reasoning", "coding")
        ],
        "ollama": settings.OLLAMA_MODEL,
    }


@router.get("/health/detailed")
def health(db: Session = Depends(get_db)):
    checks = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "up"
    except Exception:
        checks["database"] = "down"
    for name, active, factory, probe in [
        ("redis", settings.ENABLE_REDIS_CACHE, get_redis_client, lambda client: client.ping()),
        (
            "chroma",
            settings.ENABLE_SEMANTIC_RAG,
            get_chroma_client,
            lambda client: client.heartbeat(),
        ),
    ]:
        checks[name] = "disabled"
        if active:
            try:
                if not probe(factory()):
                    raise RuntimeError("Unavailable")
                checks[name] = "up"
            except Exception:
                checks[name] = "down"
    parts = urlsplit(settings.OLLAMA_URL)
    for name, url, headers in [
        ("ollama", urlunsplit((parts.scheme, parts.netloc, "/api/tags", "", "")), {}),
        (
            "nvidia",
            settings.NVIDIA_API_BASE_URL + "/models",
            {"Authorization": "Bearer " + settings.NVIDIA_API_KEY},
        ),
    ]:
        if name == "nvidia" and not settings.NVIDIA_API_KEY:
            checks[name] = "not_configured"
            continue
        try:
            response = httpx.get(url, headers=headers, timeout=2, trust_env=False)
            response.raise_for_status()
            checks[name] = "up"
        except Exception:
            checks[name] = "down"
    checks["storage"] = "up"
    try:
        store = backend()
        if store is not None:
            key = "health/" + uuid4().hex
            try:
                store.put(key, b"health", "text/plain")
                if not store.exists(key):
                    raise RuntimeError("Unavailable")
            finally:
                store.delete(key)
    except Exception:
        checks["storage"] = "down"
    return {"checks": checks}
