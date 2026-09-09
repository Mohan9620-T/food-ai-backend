import asyncio
from io import BytesIO
from time import monotonic

import httpx
import pytest
from docx import Document
from pypdf import PdfReader

from app.api import chat_documents
from app.config import settings
from app.models.chat import ChatDocumentAttachment, ChatMessageRecord, ChatSession
from app.schemas.chat import ChatHistoryMessage
from app.services.chat_document_service import ChatDocumentService
from app.services.chat_service import ChatModelUnavailableError, ChatService


def _headers(client, email="document-reliability@example.com"):
    registered = client.post(
        "/users/",
        json={"fullname": "Document User", "email": email, "password": "secret123"},
    )
    assert registered.status_code in (200, 201)
    login = client.post("/users/login", json={"email": email, "password": "secret123"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _assert_no_artifacts(db_session):
    assert db_session.query(ChatSession).count() == 0
    assert db_session.query(ChatMessageRecord).count() == 0
    assert db_session.query(ChatDocumentAttachment).count() == 0


@pytest.mark.parametrize("operation", ["generate", "summarize"])
def test_document_deadline_cancels_and_closes_upstream(monkeypatch, operation):
    state = {"closed": False, "cancelled": False}

    async def stalled(self, message, history, reference_history):
        try:
            yield "Incomplete document"
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        finally:
            state["closed"] = True

    monkeypatch.setattr(ChatService, "stream_chat", stalled)
    monkeypatch.setattr(settings, "DOCUMENT_AI_TIMEOUT_SECONDS", 0.02)
    service = ChatDocumentService()
    request = (
        service.generate_content("Create the report", [], [])
        if operation == "generate"
        else service.summarize("Company: Catering Solutions", "Summarize this")
    )
    started = monotonic()
    with pytest.raises(ChatModelUnavailableError):
        asyncio.run(request)
    assert monotonic() - started < 2
    assert state == {"closed": True, "cancelled": True}


def test_document_generation_stream_receives_saved_context(monkeypatch):
    captured = []
    history = [
        ChatHistoryMessage(role="user", content="Use the May billing period."),
        ChatHistoryMessage(role="assistant", content="The billing period is May 2026."),
    ]

    async def completed(self, message, previous, references):
        captured.append((message, previous, references))
        yield "# Billing report\n"
        yield "Company: Catering Solutions"

    monkeypatch.setattr(ChatService, "stream_chat", completed)
    result = asyncio.run(
        ChatDocumentService().generate_content(
            "Create a billing report",
            ["Company: Catering Solutions", "Status: Draft"],
            history,
            {"goal": "Maintain weight"},
        )
    )
    assert result == "# Billing report\nCompany: Catering Solutions"
    prompt, previous, references = captured[0]
    assert "Create a billing report" in prompt
    assert "Company: Catering Solutions" in prompt
    assert "Status: Draft" in prompt
    assert "Maintain weight" in prompt
    assert previous == history
    assert references == []


@pytest.mark.parametrize("chunks", [[], ["", " \n\t"]])
def test_document_generation_rejects_empty_stream(monkeypatch, chunks):
    async def empty(self, message, history, reference_history):
        for chunk in chunks:
            yield chunk

    monkeypatch.setattr(ChatService, "stream_chat", empty)
    with pytest.raises(ChatModelUnavailableError):
        asyncio.run(ChatDocumentService().generate_content("Create a report", [], []))


def test_failed_document_stream_never_returns_partial_content(monkeypatch):
    state = {"closed": False}

    async def interrupted(self, message, history, reference_history):
        try:
            yield "# Incomplete report"
            raise ChatModelUnavailableError("The upstream response was interrupted")
        finally:
            state["closed"] = True

    monkeypatch.setattr(ChatService, "stream_chat", interrupted)
    with pytest.raises(ChatModelUnavailableError):
        asyncio.run(ChatDocumentService().generate_content("Create a report", [], []))
    assert state["closed"]


@pytest.mark.parametrize("output_format", ["pdf", "docx"])
def test_export_creates_downloadable_document_without_ai_or_context_queries(
    client, monkeypatch, db_session, output_format
):
    headers = _headers(client)
    original_text = "  # Billing Report\n\nCompany: Catering Solutions\nStatus: Draft  \n"
    session_id = client.post(
        "/chat/sessions", json={"title": "Existing report"}, headers=headers
    ).json()["id"]

    def unexpected(*args, **kwargs):
        raise AssertionError("Export must not call AI or gather unrelated context")

    with monkeypatch.context() as patch:
        patch.setattr(ChatDocumentService, "generate_content", unexpected)
        patch.setattr(ChatService, "stream_chat", unexpected)
        patch.setattr(chat_documents.repository, "get_message_history", unexpected)
        patch.setattr(chat_documents.repository, "get_document_summaries", unexpected)
        patch.setattr(chat_documents.profile_service, "get", unexpected)
        result = client.post(
            "/chat/documents/generate",
            json={
                "session_id": session_id,
                "instruction": original_text,
                "output_format": output_format,
                "mode": "export",
            },
            headers=headers,
        )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["session_id"] == session_id
    assert body["response"] == original_text
    assert body["attachment"]["kind"] == "generated"
    download = client.get(f"/chat/documents/{body['attachment']['id']}/download", headers=headers)
    assert download.status_code == 200
    if output_format == "pdf":
        assert download.headers["content-type"] == "application/pdf"
        document_text = "\n".join(
            page.extract_text() for page in PdfReader(BytesIO(download.content)).pages
        )
    else:
        assert "wordprocessingml.document" in download.headers["content-type"]
        document_text = "\n".join(
            paragraph.text for paragraph in Document(BytesIO(download.content)).paragraphs
        )
    assert "# Billing Report" in document_text
    assert "Company: Catering Solutions" in document_text
    assert "Status: Draft" in document_text
    saved = db_session.query(ChatDocumentAttachment).one()
    assert saved.structured_summary == original_text
    assert saved.file_data == download.content
    history = client.get(f"/chat/sessions/{session_id}", headers=headers).json()["messages"]
    assert history[-1]["content"] == original_text
    assert history[-1]["document_attachment"]["id"] == saved.id


def test_export_can_create_a_new_session(client, monkeypatch):
    headers = _headers(client)

    async def unexpected(*args, **kwargs):
        raise AssertionError("Export must not generate AI content")

    monkeypatch.setattr(ChatDocumentService, "generate_content", unexpected)
    result = client.post(
        "/chat/documents/generate",
        json={"instruction": "Company: Catering Solutions", "mode": "export"},
        headers=headers,
    )
    assert result.status_code == 200, result.text
    assert result.json()["session_id"] > 0
    assert len(client.get("/chat/sessions", headers=headers).json()) == 1


def test_omitting_mode_still_requests_ai_generation(client, monkeypatch):
    headers = _headers(client)
    captured = []

    async def completed(self, instruction, summaries, history, profile=None):
        captured.append(instruction)
        return "# AI-created Report\nReady for review."

    monkeypatch.setattr(ChatDocumentService, "generate_content", completed)
    response = client.post(
        "/chat/documents/generate",
        json={"instruction": "Create a summary", "output_format": "pdf"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert captured == ["Create a summary"]
    assert response.json()["response"] == "# AI-created Report\nReady for review."


@pytest.mark.parametrize(
    "payload",
    [
        {"instruction": "Create a summary", "mode": "unknown"},
        {"instruction": " \t\n ", "mode": "ai"},
        {"instruction": " \t\n ", "mode": "export"},
        {"instruction": "Create a summary", "mode": "export", "output_format": "exe"},
    ],
)
def test_document_mode_validation_leaves_no_artifacts(client, db_session, payload):
    headers = _headers(client)
    response = client.post("/chat/documents/generate", json=payload, headers=headers)
    assert response.status_code == 422
    _assert_no_artifacts(db_session)


@pytest.mark.parametrize("mode", ["ai", "export"])
def test_document_mode_checks_ownership_before_processing(client, monkeypatch, mode):
    headers = _headers(client, "document-owner-mode@example.com")
    session_id = client.post(
        "/chat/sessions", json={"title": "Private report"}, headers=headers
    ).json()["id"]
    stranger_headers = _headers(client, "document-stranger-mode@example.com")

    def unexpected(*args, **kwargs):
        raise AssertionError("Unauthorized generation must not start processing")

    monkeypatch.setattr(ChatDocumentService, "generate_content", unexpected)
    monkeypatch.setattr(ChatDocumentService, "render", unexpected)
    response = client.post(
        "/chat/documents/generate",
        json={"session_id": session_id, "instruction": "Company: Private", "mode": mode},
        headers=stranger_headers,
    )
    assert response.status_code == 404


def test_nvidia_failure_and_ollama_stall_share_document_deadline(
    client, monkeypatch, db_session, caplog
):
    headers = _headers(client)
    calls = []
    state = {"closed": False, "cancelled": False}

    class StalledOllamaStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                yield b'{"message":{"content":"Partial report"},"done":false}\n'
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise

        async def aclose(self):
            state["closed"] = True

    async def handler(request):
        calls.append(request.url.host)
        if request.url.host == "nvidia.invalid":
            return httpx.Response(429, json={"error": "private-provider-error-body"})
        assert request.url.host == "ollama.invalid"
        return httpx.Response(200, stream=StalledOllamaStream())

    original_client = httpx.AsyncClient

    def mocked_client(*args, **kwargs):
        return original_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mocked_client)
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "nvidia")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr(settings, "NVIDIA_API_BASE_URL", "https://nvidia.invalid/v1")
    monkeypatch.setattr(settings, "OLLAMA_URL", "http://ollama.invalid/api/chat")
    monkeypatch.setattr(settings, "DOCUMENT_AI_TIMEOUT_SECONDS", 0.03)

    def unexpected_render(*args):
        raise AssertionError("A timed-out partial response must not become a document")

    monkeypatch.setattr(ChatDocumentService, "render", unexpected_render)
    started = monotonic()
    response = client.post(
        "/chat/documents/generate",
        json={"instruction": "Create a billing report"},
        headers=headers,
    )
    assert monotonic() - started < 2
    assert response.status_code == 503, response.text
    assert calls == ["nvidia.invalid", "ollama.invalid"]
    assert state == {"closed": True, "cancelled": True}
    _assert_no_artifacts(db_session)
    provider_errors = [
        record
        for record in caplog.records
        if record.message == "chat.text_stream_model_unavailable"
        and getattr(record, "provider", None) == "nvidia"
    ]
    assert len(provider_errors) == 1
    assert provider_errors[0].status_code == 429
    assert provider_errors[0].error_type == "HTTPStatusError"
    assert "test-only-not-a-real-key" not in caplog.text
    assert "private-provider-error-body" not in caplog.text


@pytest.mark.parametrize(
    "stream_body",
    [
        b'{"message":{"content":"Incomplete report"},"done":false}\n',
        b'{"message":{"content":"Truncated report"},"done":true,"done_reason":"length"}\n',
    ],
)
def test_incomplete_ollama_generation_is_not_saved(client, monkeypatch, db_session, stream_body):
    headers = _headers(client)
    original_client = httpx.AsyncClient

    def mocked_client(*args, **kwargs):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=stream_body))
        return original_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mocked_client)
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    response = client.post(
        "/chat/documents/generate",
        json={"instruction": "Create a complete report"},
        headers=headers,
    )
    assert response.status_code == 503, response.text
    _assert_no_artifacts(db_session)
