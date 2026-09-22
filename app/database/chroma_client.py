import logging
import re
from typing import Any, cast

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection

from app.config import settings

logger = logging.getLogger(__name__)

_client: ClientAPI | None = None


def get_chroma_client() -> ClientAPI | None:
    """Return the shared Chroma client, or None when semantic RAG is disabled.

    Callers must treat None (and any exception raised by the helpers in this
    module) as "retrieval unavailable" and fall back to the deterministic
    raw_text injection path - never let a Chroma outage block chat.
    """
    global _client
    if not settings.ENABLE_SEMANTIC_RAG:
        return None
    if _client is None:
        _client = chromadb.HttpClient(
            host=settings.CHROMA_HOST,
            port=settings.CHROMA_PORT,
        )
    return _client


def _collection_name() -> str:
    slug = re.sub(r"[^a-zA-Z0-9_]", "_", settings.EMBEDDING_MODEL)
    return f"{settings.CHROMA_COLLECTION_PREFIX}_documents_{slug}"


def get_collection(client: ClientAPI) -> Collection:
    """One collection, embedding-model-qualified so swapping providers never
    silently mixes incompatible vector spaces, filtered by metadata rather
    than split into per-tenant collections (documents here are ≤4/session,
    so per-tenant collections would be pure overhead).
    """
    return client.get_or_create_collection(
        name=_collection_name(),
        metadata={"hnsw:space": "cosine"},
    )


def require_user_scope(where: dict) -> None:
    """Guard every retrieval query against accidentally querying without a
    user_id filter - the structural cross-user isolation guarantee, checked
    at the single chokepoint rather than left to caller discipline.
    """
    if "user_id" not in where:
        raise ValueError("Chroma query is missing a required user_id scope")


def index_chunks(
    *,
    user_id: int,
    session_id: int,
    document_id: int,
    filename: str,
    ids: list[str],
    texts: list[str],
    embeddings: list[list[float]],
    metadatas: list[dict],
) -> None:
    client = get_chroma_client()
    if client is None:
        return
    try:
        collection = get_collection(client)
        collection.upsert(
            ids=ids,
            embeddings=cast(Any, embeddings),
            documents=texts,
            metadatas=cast(Any, metadatas),
        )
    except Exception:
        logger.warning(
            "rag.chroma_index_failed",
            extra={"user_id": user_id, "session_id": session_id, "document_id": document_id},
        )


def query_chunks(
    *,
    user_id: int,
    session_id: int,
    document_ids: list[int],
    query_embedding: list[float],
    top_k: int,
) -> list[dict] | None:
    """Return up to top_k chunks as {text, metadata, similarity}, best first.

    similarity is 1 - cosine_distance (the collection is configured with
    hnsw:space=cosine), so higher is more relevant, in roughly [0, 1].

    Returns None when RAG is disabled or retrieval itself failed (Chroma
    unreachable) - the caller must fall back to the deterministic raw_text
    path in that case. Returns [] (not None) when retrieval succeeded but
    found nothing - the caller should say so explicitly, not guess.
    """
    client = get_chroma_client()
    if client is None:
        return None
    where: dict = {
        "$and": [
            {"user_id": user_id},
            {"session_id": session_id},
            {"document_id": {"$in": document_ids}},
        ]
    }
    require_user_scope({"user_id": user_id})
    try:
        collection = get_collection(client)
        result = collection.query(
            query_embeddings=cast(Any, [query_embedding]),
            n_results=top_k,
            where=where,
        )
    except Exception:
        logger.warning(
            "rag.chroma_query_failed", extra={"user_id": user_id, "session_id": session_id}
        )
        return None

    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    return [
        {"text": text, "metadata": metadata, "similarity": max(0.0, 1.0 - distance)}
        for text, metadata, distance in zip(documents, metadatas, distances, strict=False)
    ]


def delete_document_chunks(*, user_id: int, document_id: int) -> None:
    client = get_chroma_client()
    if client is None:
        return
    try:
        collection = get_collection(client)
        collection.delete(where={"$and": [{"user_id": user_id}, {"document_id": document_id}]})
    except Exception:
        logger.warning(
            "rag.chroma_delete_failed", extra={"user_id": user_id, "document_id": document_id}
        )
