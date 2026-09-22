import os
from uuid import uuid4

import chromadb
import pytest

from app.config import settings
from app.database import chroma_client
from app.services.document import semantic_retrieval
from app.services.document.chunking import semantic_chunks
from app.services.document.document_operation_registry import DocumentType
from app.services.document.extraction_models import (
    DocumentBlock,
    DocumentBlockType,
    DocumentLocation,
    ExtractedDocument,
)

# --- semantic_chunks -------------------------------------------------------


def test_semantic_chunks_carries_block_location_metadata():
    extracted = ExtractedDocument(
        document_type=DocumentType.PDF,
        text="ignored when blocks are present",
        blocks=(
            DocumentBlock(
                type=DocumentBlockType.PARAGRAPH,
                text="Short paragraph.",
                location=DocumentLocation(page=3),
            ),
            DocumentBlock(
                type=DocumentBlockType.SHEET_RANGE,
                cells=(("Revenue", "100"), ("Cost", "40")),
                location=DocumentLocation(sheet="Budget", cell_range="A1:B2"),
            ),
        ),
    )

    chunks = semantic_chunks(extracted, chunk_chars=800, overlap_chars=100)

    assert [chunk.text for chunk in chunks] == ["Short paragraph.", "Revenue\t100\nCost\t40"]
    assert chunks[0].location.page == 3
    assert chunks[0].block_type == "paragraph"
    assert chunks[1].location.sheet == "Budget"
    assert chunks[1].location.cell_range == "A1:B2"
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]


def test_semantic_chunks_splits_long_blocks_with_overlap():
    long_text = " ".join(f"word{i}" for i in range(200))  # well over 100 chars
    extracted = ExtractedDocument(
        document_type=DocumentType.TXT,
        text=long_text,
        blocks=(DocumentBlock(type=DocumentBlockType.PARAGRAPH, text=long_text),),
    )

    chunks = semantic_chunks(extracted, chunk_chars=100, overlap_chars=20)

    assert len(chunks) > 1
    # Consecutive chunks share overlapping trailing/leading words.
    first_words = set(chunks[0].text.split())
    second_words = set(chunks[1].text.split())
    assert first_words & second_words
    # Every chunk stays within budget (whitespace-snapped, so <= chunk_chars).
    assert all(len(chunk.text) <= 100 for chunk in chunks)


def test_semantic_chunks_falls_back_to_paragraph_split_without_blocks():
    extracted = ExtractedDocument(
        document_type=DocumentType.TXT,
        text="First paragraph.\n\nSecond paragraph.",
        blocks=(),
    )

    chunks = semantic_chunks(extracted)

    assert [chunk.text for chunk in chunks] == ["First paragraph.", "Second paragraph."]


def test_semantic_chunks_skips_blank_blocks():
    extracted = ExtractedDocument(
        document_type=DocumentType.TXT,
        text="",
        blocks=(
            DocumentBlock(type=DocumentBlockType.PARAGRAPH, text="   "),
            DocumentBlock(type=DocumentBlockType.PARAGRAPH, text="Real content."),
        ),
    )

    chunks = semantic_chunks(extracted)

    assert [chunk.text for chunk in chunks] == ["Real content."]


# --- chroma_client -----------------------------------------------------------


@pytest.fixture
def rag_enabled(monkeypatch):
    # Exercise the same HTTP-only SDK as production against the CI Chroma service.
    # Each test owns a unique collection; existing application data is untouched.
    monkeypatch.setattr(settings, "ENABLE_SEMANTIC_RAG", True)
    prefix = f"test_{uuid4().hex}"
    monkeypatch.setattr(settings, "CHROMA_COLLECTION_PREFIX", prefix)
    client = chromadb.HttpClient(
        host=os.getenv("TEST_CHROMA_HOST", "127.0.0.1"),
        port=int(os.getenv("TEST_CHROMA_PORT", "8001")),
    )
    monkeypatch.setattr(chroma_client, "get_chroma_client", lambda: client)
    yield client
    for collection in client.list_collections():
        if collection.name.startswith(prefix):
            client.delete_collection(collection.name)


def test_get_chroma_client_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SEMANTIC_RAG", False)

    assert chroma_client.get_chroma_client() is None


def test_require_user_scope_rejects_missing_user_id():
    with pytest.raises(ValueError, match="user_id"):
        chroma_client.require_user_scope({"session_id": 1})


def test_index_and_query_round_trip(rag_enabled):
    chroma_client.index_chunks(
        user_id=1,
        session_id=5,
        document_id=10,
        filename="menu.txt",
        ids=["10:0"],
        texts=["Idli is a steamed rice cake."],
        embeddings=[[1.0, 0.0, 0.0]],
        metadatas=[{"user_id": 1, "session_id": 5, "document_id": 10, "filename": "menu.txt"}],
    )

    results = chroma_client.query_chunks(
        user_id=1, session_id=5, document_ids=[10], query_embedding=[1.0, 0.0, 0.0], top_k=5
    )

    assert results is not None
    assert len(results) == 1
    assert results[0]["text"] == "Idli is a steamed rice cake."
    assert results[0]["similarity"] > 0.99


def test_query_chunks_never_leaks_across_users(rag_enabled):
    chroma_client.index_chunks(
        user_id=1,
        session_id=5,
        document_id=10,
        filename="a.txt",
        ids=["10:0"],
        texts=["user one content"],
        embeddings=[[1.0, 0.0]],
        metadatas=[{"user_id": 1, "session_id": 5, "document_id": 10}],
    )
    chroma_client.index_chunks(
        user_id=2,
        session_id=9,
        document_id=20,
        filename="b.txt",
        ids=["20:0"],
        texts=["user two content"],
        embeddings=[[1.0, 0.0]],
        metadatas=[{"user_id": 2, "session_id": 9, "document_id": 20}],
    )

    results_for_user_one = chroma_client.query_chunks(
        user_id=1, session_id=5, document_ids=[10, 20], query_embedding=[1.0, 0.0], top_k=5
    )

    assert results_for_user_one is not None
    assert [item["text"] for item in results_for_user_one] == ["user one content"]


def test_delete_document_chunks_removes_only_matching(rag_enabled):
    chroma_client.index_chunks(
        user_id=1,
        session_id=5,
        document_id=10,
        filename="a.txt",
        ids=["10:0"],
        texts=["keep? no, delete me"],
        embeddings=[[1.0, 0.0]],
        metadatas=[{"user_id": 1, "session_id": 5, "document_id": 10}],
    )
    chroma_client.index_chunks(
        user_id=1,
        session_id=5,
        document_id=11,
        filename="b.txt",
        ids=["11:0"],
        texts=["keep me"],
        embeddings=[[0.0, 1.0]],
        metadatas=[{"user_id": 1, "session_id": 5, "document_id": 11}],
    )

    chroma_client.delete_document_chunks(user_id=1, document_id=10)

    remaining = chroma_client.query_chunks(
        user_id=1, session_id=5, document_ids=[10, 11], query_embedding=[0.0, 1.0], top_k=5
    )
    assert remaining is not None
    assert [item["text"] for item in remaining] == ["keep me"]


def test_query_chunks_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SEMANTIC_RAG", False)

    result = chroma_client.query_chunks(
        user_id=1, session_id=5, document_ids=[1], query_embedding=[0.0], top_k=5
    )

    assert result is None


# --- semantic_retrieval ------------------------------------------------------


class _StubEmbeddingProvider:
    def __init__(self, vector=(1.0, 0.0)):
        self.vector = list(vector)
        self.calls: list[tuple[list[str], str]] = []

    def embed(self, texts, *, input_type="passage"):
        self.calls.append((texts, input_type))
        return [self.vector for _ in texts]


def test_retrieve_relevant_chunks_returns_none_when_rag_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SEMANTIC_RAG", False)

    result = semantic_retrieval.retrieve_relevant_chunks(
        user_id=1, session_id=5, document_ids=[10], query="what is in this"
    )

    assert result is None


def test_retrieve_relevant_chunks_filters_by_threshold_and_caps(rag_enabled, monkeypatch):
    monkeypatch.setattr(settings, "RAG_SIMILARITY_THRESHOLD", 0.9)
    monkeypatch.setattr(settings, "RAG_TOP_K", 5)
    monkeypatch.setattr(settings, "RAG_MAX_CONTEXT_CHUNKS", 5)
    monkeypatch.setattr(settings, "RAG_MAX_CONTEXT_TOKENS", 100000)
    stub = _StubEmbeddingProvider(vector=(1.0, 0.0))
    monkeypatch.setattr(semantic_retrieval, "get_embedding_provider", lambda: stub)

    chroma_client.index_chunks(
        user_id=1,
        session_id=5,
        document_id=10,
        filename="menu.txt",
        ids=["10:0", "10:1"],
        texts=["closely matching relevant text", "totally unrelated distant text"],
        embeddings=[[1.0, 0.0], [-1.0, 0.0]],
        metadatas=[
            {"user_id": 1, "session_id": 5, "document_id": 10, "filename": "menu.txt"},
            {"user_id": 1, "session_id": 5, "document_id": 10, "filename": "menu.txt"},
        ],
    )

    result = semantic_retrieval.retrieve_relevant_chunks(
        user_id=1, session_id=5, document_ids=[10], query="what's relevant?"
    )

    assert result is not None
    assert [chunk.text for chunk in result] == ["closely matching relevant text"]
    assert stub.calls == [(["what's relevant?"], "query")]


def test_retrieve_relevant_chunks_returns_empty_list_when_nothing_matches(rag_enabled, monkeypatch):
    monkeypatch.setattr(settings, "RAG_SIMILARITY_THRESHOLD", 0.99)
    stub = _StubEmbeddingProvider(vector=(1.0, 0.0))
    monkeypatch.setattr(semantic_retrieval, "get_embedding_provider", lambda: stub)
    chroma_client.index_chunks(
        user_id=1,
        session_id=5,
        document_id=10,
        filename="menu.txt",
        ids=["10:0"],
        texts=["unrelated"],
        embeddings=[[-1.0, 0.0]],
        metadatas=[{"user_id": 1, "session_id": 5, "document_id": 10, "filename": "menu.txt"}],
    )

    result = semantic_retrieval.retrieve_relevant_chunks(
        user_id=1, session_id=5, document_ids=[10], query="anything"
    )

    assert result == []


def test_retrieve_relevant_chunks_returns_none_on_embedding_failure(rag_enabled, monkeypatch):
    class _FailingProvider:
        def embed(self, texts, *, input_type="passage"):
            raise semantic_retrieval.EmbeddingModelUnavailableError("down")

    monkeypatch.setattr(semantic_retrieval, "get_embedding_provider", lambda: _FailingProvider())

    result = semantic_retrieval.retrieve_relevant_chunks(
        user_id=1, session_id=5, document_ids=[10], query="anything"
    )

    assert result is None


def test_index_document_is_a_no_op_when_rag_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SEMANTIC_RAG", False)
    extracted = ExtractedDocument(document_type=DocumentType.TXT, text="hello")

    # Must not raise even though no embedding provider/Chroma is configured.
    semantic_retrieval.index_document(
        user_id=1, session_id=5, document_id=10, filename="a.txt", extracted=extracted
    )


def test_index_document_embeds_and_indexes_chunks(rag_enabled, monkeypatch):
    stub = _StubEmbeddingProvider(vector=(1.0, 0.0))
    monkeypatch.setattr(semantic_retrieval, "get_embedding_provider", lambda: stub)
    extracted = ExtractedDocument(
        document_type=DocumentType.TXT,
        text="ignored",
        blocks=(DocumentBlock(type=DocumentBlockType.PARAGRAPH, text="Idli is a rice cake."),),
    )

    semantic_retrieval.index_document(
        user_id=1, session_id=5, document_id=10, filename="menu.txt", extracted=extracted
    )

    results = chroma_client.query_chunks(
        user_id=1, session_id=5, document_ids=[10], query_embedding=[1.0, 0.0], top_k=5
    )
    assert results is not None
    assert results[0]["text"] == "Idli is a rice cake."
    assert results[0]["metadata"]["filename"] == "menu.txt"


# --- end-to-end: upload indexing, chat retrieval, deletion sync -------------


def _login(client, email: str) -> str:
    client.post("/users/", json={"fullname": "RAG User", "email": email, "password": "secret123"})
    return client.post("/users/login", json={"email": email, "password": "secret123"}).json()[
        "access_token"
    ]


def _upload(client, headers, filename, content, mime="text/plain"):
    return client.post(
        "/chat/documents",
        files={"file": (filename, content, mime)},
        data={"analyze": "false"},
        headers=headers,
    ).json()


def _user_id(client, token) -> int:
    import jwt

    from app.utils.security import ALGORITHM, SECRET_KEY

    return int(jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])["sub"])


def test_upload_indexes_document_when_rag_enabled(client, rag_enabled, monkeypatch):
    stub = _StubEmbeddingProvider(vector=(1.0, 0.0))
    monkeypatch.setattr(semantic_retrieval, "get_embedding_provider", lambda: stub)
    token = _login(client, "rag-upload@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    uploaded = _upload(client, headers, "menu.txt", b"Idli is a steamed rice cake.")

    document_id = uploaded["attachment"]["id"]
    session_id = uploaded["session_id"]
    results = chroma_client.query_chunks(
        user_id=_user_id(client, token),
        session_id=session_id,
        document_ids=[document_id],
        query_embedding=[1.0, 0.0],
        top_k=5,
    )
    assert results is not None
    assert results
    assert "Idli" in results[0]["text"]


def test_upload_does_not_index_when_rag_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SEMANTIC_RAG", False)
    calls = {"n": 0}
    monkeypatch.setattr(
        semantic_retrieval,
        "index_document",
        lambda **kwargs: calls.__setitem__("n", calls["n"] + 1),
    )
    token = _login(client, "rag-disabled-upload@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    _upload(client, headers, "menu.txt", b"Idli is a steamed rice cake.")

    assert calls["n"] == 0


def test_chat_injects_retrieved_chunks_instead_of_full_document(
    client, rag_enabled, monkeypatch, db_session
):
    from app.services.chat_service import ChatService

    stub = _StubEmbeddingProvider(vector=(1.0, 0.0))
    monkeypatch.setattr(semantic_retrieval, "get_embedding_provider", lambda: stub)
    token = _login(client, "rag-chat@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    long_document = "Idli is a steamed rice cake. " + ("Unrelated filler text. " * 200)
    uploaded = _upload(client, headers, "menu.txt", long_document.encode())
    session_id = uploaded["session_id"]

    captured = {}

    def fake_chat(self, message, history, reference_history, **kwargs):
        captured["reference_history"] = reference_history
        return "It's idli."

    monkeypatch.setattr(ChatService, "chat", fake_chat)
    response = client.post(
        f"/chat/?session_id={session_id}",
        json={"message": "What is idli mentioned in the document?", "history": []},
        headers=headers,
    )

    assert response.status_code == 200
    reference_texts = " ".join(item.content for item in captured["reference_history"])
    # The full 200x-repeated filler must not be injected wholesale - only the
    # small relevant excerpt should be, proving retrieval (not raw_text) fired.
    assert reference_texts.count("Unrelated filler text.") < 200
    assert "Idli" in reference_texts


def test_delete_session_removes_indexed_chunks(client, rag_enabled, monkeypatch):
    stub = _StubEmbeddingProvider(vector=(1.0, 0.0))
    monkeypatch.setattr(semantic_retrieval, "get_embedding_provider", lambda: stub)
    token = _login(client, "rag-delete@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    uploaded = _upload(client, headers, "menu.txt", b"Idli is a steamed rice cake.")
    document_id = uploaded["attachment"]["id"]
    session_id = uploaded["session_id"]
    user_id = _user_id(client, token)

    delete_response = client.delete(f"/chat/sessions/{session_id}", headers=headers)
    assert delete_response.status_code == 200

    remaining = chroma_client.query_chunks(
        user_id=user_id,
        session_id=session_id,
        document_ids=[document_id],
        query_embedding=[1.0, 0.0],
        top_k=5,
    )
    assert remaining == []
