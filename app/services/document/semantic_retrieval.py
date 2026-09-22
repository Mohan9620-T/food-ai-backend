import logging
from dataclasses import dataclass

from app.config import settings
from app.database import chroma_client
from app.services.chat_service import ChatService
from app.services.document.chunking import semantic_chunks
from app.services.document.extraction_models import ExtractedDocument
from app.services.embedding_providers import EmbeddingModelUnavailableError, get_embedding_provider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievedChunk:
    filename: str
    text: str
    similarity: float
    location_label: str


def index_document(
    *,
    user_id: int,
    session_id: int,
    document_id: int,
    filename: str,
    extracted: ExtractedDocument,
) -> None:
    """Best-effort: chunk, embed, and index a document for semantic retrieval.

    Never raises - if embeddings or Chroma are unavailable, the document is
    still saved with its deterministic raw_text extraction; it's just not
    semantically searchable until it's re-indexed.
    """
    if not settings.ENABLE_SEMANTIC_RAG:
        return
    chunks = semantic_chunks(
        extracted,
        chunk_chars=settings.RAG_CHUNK_CHARS,
        overlap_chars=settings.RAG_CHUNK_OVERLAP_CHARS,
    )
    if not chunks:
        return

    try:
        embeddings = get_embedding_provider().embed(
            [chunk.text for chunk in chunks], input_type="passage"
        )
    except EmbeddingModelUnavailableError:
        logger.warning("rag.index_embedding_failed", extra={"document_id": document_id})
        return

    ids = [f"{document_id}:{chunk.chunk_index}" for chunk in chunks]
    metadatas = [
        {
            "user_id": user_id,
            "session_id": session_id,
            "document_id": document_id,
            "filename": filename,
            "chunk_index": chunk.chunk_index,
            "block_type": chunk.block_type,
            "page": chunk.location.page,
            "sheet": chunk.location.sheet,
            "cell_range": chunk.location.cell_range,
        }
        for chunk in chunks
    ]
    chroma_client.index_chunks(
        user_id=user_id,
        session_id=session_id,
        document_id=document_id,
        filename=filename,
        ids=ids,
        texts=[chunk.text for chunk in chunks],
        embeddings=embeddings,
        metadatas=metadatas,
    )


def _location_label(metadata: dict) -> str:
    parts = []
    if metadata.get("page"):
        parts.append(f"page {metadata['page']}")
    if metadata.get("sheet"):
        parts.append(f"sheet {metadata['sheet']}")
    if metadata.get("cell_range"):
        parts.append(str(metadata["cell_range"]))
    return ", ".join(parts)


def retrieve_relevant_chunks(
    *,
    user_id: int,
    session_id: int,
    document_ids: list[int],
    query: str,
) -> list[RetrievedChunk] | None:
    """Return relevant chunks across the given documents, best first.

    Respects RAG_SIMILARITY_THRESHOLD, RAG_MAX_CONTEXT_CHUNKS, and
    RAG_MAX_CONTEXT_TOKENS.

    Returns None when RAG is disabled or retrieval itself is unavailable
    (embedding provider or Chroma unreachable) - the caller must fall back
    to the deterministic raw_text path in that case, exactly like today.
    Returns [] (not None) when retrieval succeeded but nothing cleared the
    threshold - the caller should say so explicitly, never guess and never
    silently fall back to injecting the full document (that would defeat
    semantic retrieval's purpose).
    """
    if not settings.ENABLE_SEMANTIC_RAG or not document_ids:
        return None

    try:
        embeddings = get_embedding_provider().embed([query], input_type="query")
    except EmbeddingModelUnavailableError:
        logger.warning("rag.query_embedding_failed", extra={"session_id": session_id})
        return None
    if not embeddings:
        return None

    raw = chroma_client.query_chunks(
        user_id=user_id,
        session_id=session_id,
        document_ids=document_ids,
        query_embedding=embeddings[0],
        top_k=settings.RAG_TOP_K,
    )
    if raw is None:
        return None

    selected: list[RetrievedChunk] = []
    token_total = 0
    for item in raw:
        if item["similarity"] < settings.RAG_SIMILARITY_THRESHOLD:
            continue
        if len(selected) >= settings.RAG_MAX_CONTEXT_CHUNKS:
            break
        tokens = ChatService._estimate_tokens(item["text"])
        if selected and token_total + tokens > settings.RAG_MAX_CONTEXT_TOKENS:
            break
        token_total += tokens
        metadata = item["metadata"]
        selected.append(
            RetrievedChunk(
                filename=str(metadata.get("filename", "document")),
                text=item["text"],
                similarity=item["similarity"],
                location_label=_location_label(metadata),
            )
        )
    return selected
