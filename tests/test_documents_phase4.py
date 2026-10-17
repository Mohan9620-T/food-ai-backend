"""Phase 4 security and failure-path tests; no provider or Docker is contacted."""

import asyncio
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openpyxl import Workbook

from app.config import settings
from app.models.chat import (
    ChatDocumentAttachment,
    ChatImageAttachment,
    ChatMessageRecord,
    ChatSession,
)
from app.repositories.library_repository import LibraryRepository
from app.services.document.citations import REFUSAL, Citation, support, validate_answer
from app.services.document.extraction_models import StructuredDocumentContent
from app.services.document.phase4_turn import file_questions
from app.services.document.sandbox import (
    DisabledSandboxRunner,
    SandboxResult,
    execute,
    extract_code,
    introspect,
)
from app.services.document.table_review import apply_review, review_content
from app.services.document.upload_validation import validate_document_container
from app.storage import LocalBlobStorage, backend, read_blob, store_blob
from app.utils.document_clarification import file_request_gate, validate_file_questions


@pytest.mark.parametrize(
    "message",
    [
        "Make an Excel of breakfasts",
        "save as PDF",
        "turn this into a CSV",
        "create a word document",
        "generate an xlsx file",
        "Create a breakfast report",
        "breakfast ideas in markdown",
    ],
)
def test_pattern_gate(message):
    assert file_request_gate(message)


@pytest.mark.parametrize(
    "message",
    [
        "hello",
        "What is an Excel workbook?",
        "Tell me about breakfast",
        "write a poem",
        "how are you",
    ],
)
def test_chat_does_not_pay_for_decision(message):
    complete = AsyncMock()
    assert asyncio.run(file_questions(message, complete)) == []
    complete.assert_not_called()


def questions():
    return [
        {
            "question": "Which scope?",
            "options": [
                {"label": "Vegetarian", "recommended": True},
                {"label": "Any", "recommended": False},
            ],
        }
    ]


def test_question_contract_and_recommended_default(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_FILE_CLARIFY", True)
    complete = AsyncMock(return_value=json.dumps({"questions": questions()}))
    assert asyncio.run(file_questions("Make an Excel of breakfasts", complete)) == questions()
    assert len(validate_file_questions(questions() * 3)) == 3
    malformed = questions()
    malformed[0]["options"][1]["recommended"] = True
    normalized = validate_file_questions(malformed)
    assert sum(option["recommended"] for option in normalized[0]["options"]) == 1


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        [],
        [{}],
        questions() * 4,
        [{"question": "x", "options": []}],
        [{"question": "x", "options": [{"label": ""}, {"label": "ok"}]}],
    ],
)
def test_invalid_questions(invalid):
    with pytest.raises(ValueError):
        validate_file_questions(invalid)


@pytest.mark.parametrize("reply", ["not json", "{}", '{"questions":[{}]}'])
def test_failed_clarifier_keeps_existing_flow(monkeypatch, reply):
    monkeypatch.setattr(settings, "ENABLE_FILE_CLARIFY", True)
    assert asyncio.run(file_questions("Make an Excel", AsyncMock(return_value=reply))) == []


def test_disabled_clarifier(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_FILE_CLARIFY", False)
    complete = AsyncMock()
    assert asyncio.run(file_questions("Make an Excel", complete)) == []
    complete.assert_not_called()


def test_table_review_only_removes_or_reorders():
    rows = [["B", "10"], ["A", "2"], ["C", ""]]
    result = apply_review(
        ["Name", "Count"], rows, {"drop": [1], "sort": [{"column": "Count", "descending": True}]}
    )
    assert result == [rows[1], rows[2]]
    assert rows == [["B", "10"], ["A", "2"], ["C", ""]]
    assert apply_review(
        ["Name", "Count"], rows, {"drop": [], "sort": [{"column": "Name", "descending": False}]}
    ) == [rows[1], rows[0], rows[2]]


@pytest.mark.parametrize(
    "verdict",
    [
        {},
        {"drop": [0], "sort": []},
        {"drop": [True], "sort": []},
        {"drop": [4], "sort": []},
        {"drop": [], "sort": [{"column": "invented", "descending": False}]},
        {"drop": [], "sort": [{"column": "Name", "descending": "false"}]},
    ],
)
def test_invalid_table_verdict(verdict):
    with pytest.raises(ValueError):
        apply_review(["Name"], [["A"]], verdict)


def test_table_review_failure_is_atomic(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_TABLE_REVIEW", True)
    content = StructuredDocumentContent(
        title="Example", tables=[{"headers": ["Name"], "rows": [["A"], ["B"]]}]
    )
    assert (
        asyncio.run(review_content(content, "names", AsyncMock(return_value="invalid"))) == content
    )
    reviewed = asyncio.run(
        review_content(content, "names", AsyncMock(return_value='{"drop":[1],"sort":[]}'))
    )
    assert reviewed.tables[0].rows == [["B"]]
    monkeypatch.setattr(settings, "ENABLE_TABLE_REVIEW", False)
    complete = AsyncMock()
    assert asyncio.run(review_content(content, "names", complete)) == content
    complete.assert_not_called()


def workbook():
    book = Workbook()
    sheet = book.active
    sheet.title = "Exact sheet"
    sheet.append(["Dish", "Count"])
    for index in range(8):
        sheet.append(["Meal", index])
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


def test_introspection_keeps_real_columns_and_full_row_count(monkeypatch):
    monkeypatch.setattr(settings, "SANDBOX_SEND_SAMPLES", True)
    sheet = introspect(workbook(), ".xlsx")[0]
    assert sheet["sheet"] == "Exact sheet" and sheet["rows"] == 8
    assert sheet["columns"] == ["Dish", "Count"] and sheet["dtypes"] == [["str"], ["int"]]
    assert len(sheet["sample"]) == 5
    monkeypatch.setattr(settings, "SANDBOX_SEND_SAMPLES", False)
    assert introspect(b"A,B\n1,2\n3,4\n", ".csv")[0]["sample"] == []


def test_code_fences_and_ast_validation():
    assert extract_code("```python\nprint(1)\n```") == "print(1)"
    with pytest.raises(SyntaxError):
        extract_code("this is not Python !")
    assert not DisabledSandboxRunner().run("", b"", ".csv").available


def test_sandbox_one_repair_and_disabled_fallback(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SANDBOX_EXECUTION", True)

    class FakeRunner:
        def __init__(self):
            self.calls = 0

        def run(self, code, data, extension):
            self.calls += 1
            return (
                SandboxResult(error="wrong column")
                if self.calls == 1
                else SandboxResult(answer='{"count":8}')
            )

    runner = FakeRunner()
    complete = AsyncMock(return_value="print(8)")
    result = asyncio.run(execute(workbook(), "input.xlsx", "count", complete, runner))
    assert result.answer == '{"count":8}' and runner.calls == 2
    assert "wrong column" in complete.call_args.args[0]
    monkeypatch.setattr(settings, "ENABLE_SANDBOX_EXECUTION", False)
    assert not asyncio.run(execute(b"", "x.csv", "count", complete, runner)).available


def test_citations_validate_support_markers_and_refusal():
    sources = [
        Citation("[S1]", 3, "menu.pdf", "page 2", "Breakfast costs 12 dollars and includes coffee.")
    ]
    answer, cited = validate_answer("Breakfast costs 12 dollars [S1].", sources)
    assert answer != REFUSAL and cited[0]["document_id"] == 3
    assert validate_answer("Breakfast costs 12 dollars.", sources)[1]
    assert validate_answer("Breakfast costs 99 dollars [S1].", sources) == (REFUSAL, [])
    assert validate_answer("Breakfast costs 12 dollars [S9].", sources) == (REFUSAL, [])
    assert validate_answer("Dinner includes lobster.", sources) == (REFUSAL, [])
    assert validate_answer(REFUSAL, sources) == (REFUSAL, [])
    assert support("Breakfast 99", "Breakfast 12") == 0


@pytest.mark.parametrize("key", ["../outside", "/outside", "", "."])
def test_local_storage_rejects_traversal(tmp_path, key):
    store = LocalBlobStorage(str(tmp_path))
    with pytest.raises(ValueError):
        store.put(key, b"data", "text/plain")


def test_blob_backends_and_legacy_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_LOCAL_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    key = store_blob(b"private data", "text/plain")
    assert key and read_blob(key, None) == b"private data"
    assert read_blob(None, b"legacy") == b"legacy"
    store = backend()
    relative = key.split(":", 1)[1]
    assert store.exists(relative) and store.signed_url(relative, 300) is None
    store.delete(relative)
    assert not store.exists(relative)
    assert read_blob(key, b"fallback") == b"fallback"
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "database")
    assert store_blob(b"data", "text/plain") is None
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "missing")
    assert store_blob(b"data", "text/plain") is None


def test_model_read_write_and_library_ownership(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "STORAGE_LOCAL_DIR", str(tmp_path))
    session = ChatSession(user_id=7, title="Private")
    other = ChatSession(user_id=8, title="Other")
    db_session.add_all([session, other])
    db_session.flush()
    message = ChatMessageRecord(
        session_id=session.id,
        sender="user",
        content="upload",
        image_data=b"image",
        image_content_type="image/png",
    )
    db_session.add(message)
    db_session.flush()
    document = ChatDocumentAttachment(
        session_id=session.id,
        message_id=message.id,
        filename="private.txt",
        content_type="text/plain",
        file_size=5,
        kind="uploaded",
        file_data=b"hello",
        raw_text="hello",
    )
    extra = ChatImageAttachment(
        message_id=message.id, image_data=b"second", content_type="image/png"
    )
    db_session.add_all([document, extra])
    db_session.commit()
    assert document.storage_key and document._file_data is None and document.file_data == b"hello"
    assert message.image_data == b"image" and extra.image_data == b"second"
    repo = LibraryRepository()
    result = repo.list(db_session, 7, None, "", 30, 0)
    assert result["total"] == 2
    assert repo.list(db_session, 8, None, "", 30, 0)["total"] == 0
    assert repo.get(db_session, 8, "document", str(document.id)) is None
    assert repo.get(db_session, 7, "generated", str(document.id)) is None
    assert repo.get(db_session, 7, "document", str(document.id))[1] == b"hello"
    assert repo.list(db_session, 7, "document", "private", 1, 0)["total"] == 1
    assert repo.list(db_session, 7, "document", "%", 1, 0)["total"] == 0
    assert repo.list(db_session, 7, "image", "", 30, 0)["total"] == 1
    assert repo.get(db_session, 7, "image", "i" + str(extra.id))[1] == b"second"


def test_upload_validation_is_cheap_and_rejects_spoofed_content():
    for extension in (".xlsx", ".pdf", ".docx", ".pptx"):
        with pytest.raises(Exception):
            validate_document_container(b"not this format", extension)
    validate_document_container(workbook(), ".xlsx")
    validate_document_container(b"A,B\n1,2", ".csv")


def test_worker_failure_never_sticks_processing(db_session, monkeypatch):
    from app.services.document.processing import process_one

    session = ChatSession(user_id=1, title="test")
    db_session.add(session)
    db_session.flush()
    message = ChatMessageRecord(session_id=session.id, sender="user", content="file")
    db_session.add(message)
    db_session.flush()
    document = ChatDocumentAttachment(
        session_id=session.id,
        message_id=message.id,
        filename="bad.xlsx",
        content_type="application/octet-stream",
        file_size=3,
        file_data=b"bad",
        processing_status="queued",
    )
    db_session.add(document)
    db_session.commit()
    process_one(db_session, int(document.id))
    db_session.refresh(document)
    assert document.processing_status == "failed" and document.processing_error


@pytest.mark.skipif(__import__("shutil").which("docker") is None, reason="Docker not installed")
def test_optional_docker_sandbox():
    import os

    if os.getenv("RUN_SANDBOX_INTEGRATION") != "true":
        pytest.skip("Set RUN_SANDBOX_INTEGRATION=true with a prebuilt SANDBOX_IMAGE")
    from app.services.document.sandbox import DockerSandboxRunner

    data = ("Name,Count\n" + "\n".join(f"Meal{i},{i}" for i in range(17))).encode()
    script = "import pandas as pd, json\nframe = pd.read_csv('/work/input.csv', keep_default_na=False)\nprint(json.dumps({'count': len(frame)}))"
    result = DockerSandboxRunner().run(script, data, ".csv")
    assert result.answer == '{"count": 17}', result.error
    output = DockerSandboxRunner().run(
        script + "\nframe.to_csv('/work/output.csv', index=False)", data, ".csv"
    )
    assert output.data and len(output.data.decode().splitlines()) == 18, output.error


@pytest.fixture
def document_api(client, db_session):
    from app.main import app
    from app.utils.auth_dependency import get_current_user

    app.dependency_overrides[get_current_user] = lambda: {"sub": "1"}
    session = ChatSession(user_id=1, title="My files")
    foreign = ChatSession(user_id=2, title="Private files")
    db_session.add_all([session, foreign])
    db_session.flush()
    message = ChatMessageRecord(session_id=session.id, sender="user", content="upload")
    secret = ChatMessageRecord(session_id=foreign.id, sender="user", content="private upload")
    db_session.add_all([message, secret])
    db_session.flush()
    document = ChatDocumentAttachment(
        session_id=session.id,
        message_id=message.id,
        filename="notes.txt",
        content_type="text/plain",
        file_size=24,
        kind="uploaded",
        file_data=b"Breakfast includes tea.",
        raw_text="Breakfast includes tea.",
    )
    other = ChatDocumentAttachment(
        session_id=foreign.id,
        message_id=secret.id,
        filename="secret.txt",
        content_type="text/plain",
        file_size=6,
        kind="uploaded",
        file_data=b"secret",
        raw_text="secret",
    )
    db_session.add_all([document, other])
    db_session.commit()
    yield client, session, document, other
    app.dependency_overrides.pop(get_current_user, None)


def test_library_endpoints_are_flagged_and_owned(document_api, monkeypatch):
    client, session, document, other = document_api
    monkeypatch.setattr(settings, "ENABLE_LIBRARY", False)
    assert client.get("/library").status_code == 404
    assert client.get("/library/capabilities").json()["library"] is False
    monkeypatch.setattr(settings, "ENABLE_LIBRARY", True)
    page = client.get("/library?kind=document&q=notes").json()
    assert page["total"] == 1 and page["items"][0]["session_id"] == session.id
    for action in ["download", "preview"]:
        assert client.get(f"/library/document/{other.id}/{action}").status_code == 404
        assert client.get(f"/library/generated/{document.id}/{action}").status_code == 404
    assert (
        client.get(f"/library/document/{document.id}/download").content
        == b"Breakfast includes tea."
    )
    assert (
        client.get(f"/library/document/{document.id}/preview").json()["text"]
        == "Breakfast includes tea."
    )
    assert client.get("/library?limit=101").status_code == 422
    monkeypatch.setattr(settings, "ENABLE_BACKGROUND_DOCUMENT_PROCESSING", True)
    assert client.get(f"/chat/documents/{other.id}/status").status_code == 404
    assert (
        client.get(f"/chat/documents/{document.id}/status").json()["processing_status"] == "ready"
    )


@pytest.mark.parametrize("stream", [False, True])
def test_clarify_is_persisted_and_streamed(document_api, monkeypatch, stream):
    from app.services.chat_document_service import ChatDocumentService

    client, session, document, other = document_api
    monkeypatch.setattr(settings, "ENABLE_FILE_CLARIFY", True)
    monkeypatch.setattr(
        ChatDocumentService,
        "_complete",
        AsyncMock(return_value=json.dumps({"questions": questions()})),
    )
    url = "/chat/stream" if stream else "/chat/"
    response = client.post(
        f"{url}?session_id={session.id}", json={"message": "Make an Excel of breakfasts"}
    )
    assert response.status_code == 200
    if stream:
        events = [json.loads(line) for line in response.text.splitlines()]
        assert [e["type"] for e in events] == ["session", "meta", "token", "clarify", "done"]
        assert events[-2]["questions"] == questions()
    else:
        assert response.json()["file_clarification"]["questions"] == questions()
    restored = client.get(f"/chat/sessions/{session.id}").json()
    assert restored["messages"][-1]["file_clarification"]["questions"] == questions()


def test_clarification_answers_resume_build(document_api, monkeypatch):
    from app.services.chat_document_service import ChatDocumentService

    client, session, document, other = document_api
    monkeypatch.setattr(settings, "ENABLE_FILE_CLARIFY", True)
    complete = AsyncMock(return_value=json.dumps({"questions": questions()}))
    monkeypatch.setattr(ChatDocumentService, "_complete", complete)
    client.post(f"/chat/?session_id={session.id}", json={"message": "Make an Excel of breakfasts"})
    complete.return_value = json.dumps(
        {"title": "Breakfasts", "tables": [{"headers": ["Meal"], "rows": [["Oats"]]}]}
    )
    response = client.post(
        f"/chat/stream?session_id={session.id}", json={"message": "Which scope?: Vegetarian"}
    )
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[1]["type"] == "meta" and events[-1]["type"] == "done"
    assert any(event["type"] == "artifacts" for event in events)
    restored = client.get(f"/chat/sessions/{session.id}").json()
    assert any(
        message["file_clarification"] and message["file_clarification"]["resolved"]
        for message in restored["messages"]
    )


@pytest.mark.parametrize("status", ["queued", "processing", "failed"])
def test_questions_wait_for_processing(document_api, monkeypatch, db_session, status):
    client, session, document, other = document_api
    monkeypatch.setattr(settings, "ENABLE_BACKGROUND_DOCUMENT_PROCESSING", True)
    setattr(document, "processing_status", status)
    db_session.commit()
    response = client.post(
        f"/chat/?session_id={session.id}", json={"message": "Summarize this document"}
    )
    assert response.status_code == 200
    assert ("failed" if status == "failed" else "still processing") in response.json()["response"]


@pytest.mark.parametrize("stream", [False, True])
def test_grounded_turn_persists_citations(document_api, monkeypatch, stream):
    from app.services.chat_document_service import ChatDocumentService

    client, session, document, other = document_api
    monkeypatch.setattr(settings, "ENABLE_DOCUMENT_CITATIONS", True)
    monkeypatch.setattr(settings, "ENABLE_SEMANTIC_RAG", False)
    monkeypatch.setattr(
        ChatDocumentService, "_complete", AsyncMock(return_value="Breakfast includes tea [S1].")
    )
    url = "/chat/stream" if stream else "/chat/"
    response = client.post(
        f"{url}?session_id={session.id}", json={"message": "Summarize the document"}
    )
    assert response.status_code == 200
    if stream:
        events = [json.loads(line) for line in response.text.splitlines()]
        citation_event = next(e for e in events if e["type"] == "citations")
        assert citation_event["items"][0]["document_id"] == document.id
    else:
        assert response.json()["citations"][0]["document_id"] == document.id
    restored = client.get(f"/chat/sessions/{session.id}").json()
    assert restored["messages"][-1]["citations"][0]["excerpt"] == "Breakfast includes tea."


def test_background_upload_returns_before_extraction(document_api, monkeypatch):
    from app.services.chat_document_service import ChatDocumentService

    client, session, document, other = document_api
    monkeypatch.setattr(settings, "ENABLE_BACKGROUND_DOCUMENT_PROCESSING", True)
    monkeypatch.setattr(
        ChatDocumentService,
        "extract_document",
        lambda *args, **kwargs: pytest.fail("Upload must not extract synchronously"),
    )
    response = client.post(
        "/chat/documents",
        files={"file": ("pending.txt", b"Pending contents", "text/plain")},
        data={"session_id": session.id},
    )
    assert response.status_code == 200
    attachment = response.json()["attachment"]
    assert attachment["processing_status"] == "queued"
    status = client.get(f"/chat/documents/{attachment['id']}/status").json()
    assert status["processing_stage"] == "queued"


def test_worker_extracts_and_marks_ready(document_api, db_session, monkeypatch):
    from app.services.document import processing
    from app.services.document.processing import process_one

    client, session, document, other = document_api
    monkeypatch.setattr(processing, "index_document", lambda **kwargs: None)
    setattr(document, "processing_status", "queued")
    db_session.commit()
    process_one(db_session, int(document.id))
    db_session.refresh(document)
    assert document.processing_status == "ready" and document.processing_stage == "complete"
    assert document.extracted_data
    process_one(db_session, 99999)


def test_sandbox_disabled_never_calls_model(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SANDBOX_EXECUTION", True)
    complete = AsyncMock()
    assert not asyncio.run(
        execute(b"", "x.csv", "compute", complete, DisabledSandboxRunner())
    ).available
    complete.assert_not_called()


@pytest.mark.parametrize("mode", ["success", "failure", "timeout"])
def test_docker_runner_isolation_and_cleanup(monkeypatch, mode):
    import subprocess

    from app.services.document import sandbox

    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "docker")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1] == "rm":
            return SimpleNamespace(returncode=0)
        assert "--read-only" in command and command[command.index("--network") + 1] == "none"
        assert command[command.index("--memory") + 1] == settings.SANDBOX_MEMORY
        assert command[command.index("--cpus") + 1] == settings.SANDBOX_CPUS
        assert "--tmpfs" in command and "--cap-drop" in command and "--security-opt" in command
        mount = command[command.index("--mount") + 1]
        folder = mount.split("source=", 1)[1].split(",target=", 1)[0]
        assert [file.name for file in __import__("pathlib").Path(folder).iterdir()] == ["input.csv"]
        assert "shell" not in kwargs
        if mode == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        if mode == "failure":
            kwargs["stderr"].write(b"wrong column")
            return SimpleNamespace(returncode=1)
        kwargs["stdout"].write(b'{"count":2}')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(sandbox.subprocess, "run", run)
    result = sandbox.DockerSandboxRunner().run('print("result")', b"Name\nA\nB", ".csv")
    assert commands[-1][1:3] == ["rm", "-f"]
    if mode == "success":
        assert result.answer == '{"count": 2}'
    else:
        assert result.error


def test_s3_backend_uses_sdk_credentials_and_signed_links(monkeypatch):
    import sys

    from app.storage import S3BlobStorage

    class Client:
        objects = {}

        def put_object(self, **kwargs):
            self.objects[kwargs["Key"]] = kwargs["Body"]

        def get_object(self, **kwargs):
            return {"Body": BytesIO(self.objects[kwargs["Key"]])}

        def delete_object(self, **kwargs):
            self.objects.pop(kwargs["Key"], None)

        def head_object(self, **kwargs):
            return {}

        def generate_presigned_url(self, *args, **kwargs):
            return "https://storage.example/temporary"

    client = Client()
    monkeypatch.setitem(
        sys.modules, "boto3", SimpleNamespace(client=lambda *args, **kwargs: client)
    )
    monkeypatch.setattr(settings, "AWS_S3_BUCKET", "test-bucket")
    store = S3BlobStorage()
    store.put("key", b"bytes", "text/plain")
    assert store.get_bytes("key") == b"bytes" and store.exists("key")
    assert store.signed_url("key", 60) == "https://storage.example/temporary"
    store.delete("key")
    assert not client.objects


def test_migration_script_is_idempotent_and_has_dry_run(
    document_api, db_session, monkeypatch, tmp_path
):
    from contextlib import contextmanager

    from scripts import migrate_blobs_to_storage as script

    _, _, document, _ = document_api

    @contextmanager
    def session():
        yield db_session

    monkeypatch.setattr(script, "SessionLocal", session)
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "STORAGE_LOCAL_DIR", str(tmp_path))
    assert script.migrate(dry_run=True) == 2
    assert document.storage_key is None
    assert script.migrate() == 2
    assert script.migrate() == 0
    assert document.file_data == b"Breakfast includes tea."


def test_worker_lifecycle_and_redis_fallback(document_api, db_session, monkeypatch):
    from contextlib import contextmanager

    from app.services.document import processing

    _, _, document, _ = document_api

    @contextmanager
    def session():
        yield db_session

    monkeypatch.setattr(settings, "ENABLE_BACKGROUND_DOCUMENT_PROCESSING", True)
    monkeypatch.setattr(settings, "ENABLE_REDIS_CACHE", True)
    monkeypatch.setattr(processing, "get_redis_client", lambda: None)
    monkeypatch.setattr(processing, "index_document", lambda **kwargs: None)
    processing.enqueue(int(document.id))
    setattr(document, "processing_status", "processing")
    db_session.commit()

    async def lifecycle():
        worker = processing.DocumentWorker(session)
        await worker.start()
        await worker.stop()

    asyncio.run(lifecycle())
    db_session.refresh(document)
    assert document.processing_status == "failed"
    setattr(document, "processing_status", "queued")
    db_session.commit()
    processing.DocumentWorker(session).tick()
    db_session.refresh(document)
    assert document.processing_status == "ready"


def test_sandbox_data_answers_only_after_deterministic_gate(document_api, monkeypatch):
    from app.services.document import phase4_turn
    from app.services.document.document_intent_service import DocumentIntentService
    from app.services.document.document_operation_registry import DocumentOperation

    client, session, document, _ = document_api
    monkeypatch.setattr(settings, "ENABLE_SANDBOX_EXECUTION", True)
    # Force source metadata to spreadsheet for the test, without a real Docker call.
    setattr(document, "filename", "data.xlsx")
    setattr(document, "file_data", workbook())
    called = AsyncMock(return_value=SandboxResult(answer='{"count":8}'))
    monkeypatch.setattr(phase4_turn, "execute", called)
    monkeypatch.setattr(
        DocumentIntentService,
        "resolve_plan",
        lambda *args, **kwargs: SimpleNamespace(
            steps=[SimpleNamespace(operation=DocumentOperation.ANSWER_DOCUMENT_QUESTION)]
        ),
    )
    response = client.post(
        f"/chat/?session_id={session.id}", json={"message": "How many rows in the workbook?"}
    )
    assert response.status_code == 200 and response.json()["response"] == '{"count":8}'
    called.assert_awaited_once()


@pytest.mark.parametrize(
    "content_type,filename,data",
    [("image/png", "photo.png", b"png"), ("application/pdf", "report.pdf", b"%PDF-1.7")],
)
def test_library_binary_previews(
    document_api, monkeypatch, db_session, content_type, filename, data
):
    client, _, document, _ = document_api
    monkeypatch.setattr(settings, "ENABLE_LIBRARY", True)
    setattr(document, "content_type", content_type)
    setattr(document, "filename", filename)
    setattr(document, "file_data", data)
    db_session.commit()
    kind = "image" if content_type.startswith("image/") else "document"
    response = client.get(f"/library/{kind}/{document.id}/preview")
    assert response.content == data and response.headers["x-content-type-options"] == "nosniff"


def test_library_spreadsheet_preview_and_signed_download(document_api, monkeypatch, db_session):
    from app.api import library

    client, _, document, _ = document_api
    monkeypatch.setattr(settings, "ENABLE_LIBRARY", True)
    setattr(document, "filename", "menu.xlsx")
    setattr(document, "file_data", workbook())
    db_session.commit()
    response = client.get(f"/library/document/{document.id}/preview")
    assert response.status_code == 200 and response.json()["sheet"] == "Exact sheet"
    setattr(document, "storage_key", "s3:private-key")
    db_session.commit()
    monkeypatch.setattr(
        library,
        "backend",
        lambda name: SimpleNamespace(signed_url=lambda key, ttl: "https://signed.example/file"),
    )
    # get(read_data=False) must not load inaccessible bytes before making a link.
    response = client.get(f"/library/document/{document.id}/download", follow_redirects=False)
    assert (
        response.status_code == 307
        and response.headers["location"] == "https://signed.example/file"
    )


def test_semantic_excerpts_use_ids_not_duplicate_filenames(monkeypatch):
    from app.services.document import citations
    from app.services.document.semantic_retrieval import RetrievedChunk

    first = SimpleNamespace(id=1, filename="menu.txt")
    second = SimpleNamespace(id=2, filename="menu.txt")
    monkeypatch.setattr(
        citations,
        "retrieve_relevant_chunks",
        lambda **kwargs: [
            RetrievedChunk("menu.txt", "Tea costs 12", 0.9, "sheet Menu", document_id=2)
        ],
    )
    result = citations.excerpts([first, second], 1, 1, "tea")
    assert result[0].document_id == 2 and result[0].location == "sheet Menu"
    monkeypatch.setattr(citations, "retrieve_relevant_chunks", lambda **kwargs: [])
    assert citations.excerpts([first, second], 1, 1, "tea") == []


def test_document_block_locations_are_retained(monkeypatch):
    from app.services.document import citations

    monkeypatch.setattr(citations, "retrieve_relevant_chunks", lambda **kwargs: None)
    document = SimpleNamespace(
        id=1,
        filename="menu.xlsx",
        extracted_data={"blocks": [{"text": "Tea costs 12", "location": {"sheet": "Menu"}}]},
    )
    assert citations.excerpts([document], 1, 1, "tea")[0].location == "sheet Menu"


def test_sandbox_file_output_is_saved_as_a_normal_generated_file(document_api, monkeypatch):
    from app.services.document import phase4_turn
    from app.services.document.document_intent_service import DocumentIntentService
    from app.services.document.document_operation_registry import DocumentOperation

    client, session, document, _ = document_api
    monkeypatch.setattr(settings, "ENABLE_SANDBOX_EXECUTION", True)
    setattr(document, "filename", "data.xlsx")
    setattr(
        document,
        "content_type",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    setattr(document, "file_data", workbook())
    monkeypatch.setattr(
        phase4_turn, "execute", AsyncMock(return_value=SandboxResult(data=workbook()))
    )
    monkeypatch.setattr(
        DocumentIntentService,
        "resolve_plan",
        lambda *args, **kwargs: SimpleNamespace(
            steps=[SimpleNamespace(operation=DocumentOperation.ANSWER_DOCUMENT_QUESTION)],
            needs_clarification=False,
        ),
    )
    response = client.post(
        f"/chat/stream?session_id={session.id}", json={"message": "Compute rows in the workbook"}
    )
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    artifact = next(event for event in events if event["type"] == "artifacts")["items"][0]
    assert artifact["kind"] == "generated"
    assert client.get(f"/chat/documents/{artifact['id']}/download").status_code == 200


def test_unsupported_sandbox_gets_one_repair_and_no_column_loss(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SANDBOX_EXECUTION", True)

    class FakeRunner:
        calls = 0

        def run(self, code, data, extension):
            self.calls += 1
            return SandboxResult(data=b"LostColumn\nx")

    runner = FakeRunner()
    result = asyncio.run(
        execute(
            b"Name,Count\nA,2", "table.csv", "compute", AsyncMock(return_value="print(1)"), runner
        )
    )
    assert result.error and runner.calls == 2


def test_multi_table_review_uses_one_model_call(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_TABLE_REVIEW", True)
    content = StructuredDocumentContent(
        title="Meals",
        tables=[
            {"headers": ["Name"], "rows": [["A"], ["B"]]},
            {"headers": ["Name"], "rows": [["C"], ["D"]]},
        ],
    )
    complete = AsyncMock(return_value='{"drop":[3],"sort":[]}')
    reviewed = asyncio.run(review_content(content, "Only requested entries", complete))
    assert reviewed.tables[0].rows == [["A"], ["B"]] and reviewed.tables[1].rows == [["D"]]
    complete.assert_awaited_once()


def test_automation_uses_sandbox_only_after_deterministic_gate(
    document_api, monkeypatch, db_session
):
    from app.services.document import phase4_turn
    from app.services.document.document_intent_service import DocumentIntentService
    from app.services.document.document_operation_registry import DocumentOperation

    client, session, document, _ = document_api
    monkeypatch.setattr(settings, "ENABLE_SANDBOX_EXECUTION", True)
    setattr(document, "filename", "data.xlsx")
    setattr(document, "file_data", workbook())
    db_session.commit()
    monkeypatch.setattr(
        phase4_turn, "execute", AsyncMock(return_value=SandboxResult(answer='{"count":8}'))
    )
    monkeypatch.setattr(
        DocumentIntentService,
        "resolve_plan",
        lambda *args, **kwargs: SimpleNamespace(
            steps=[SimpleNamespace(operation=DocumentOperation.ANSWER_DOCUMENT_QUESTION)],
            needs_clarification=False,
        ),
    )
    response = client.post(
        "/chat/documents/automate",
        json={
            "session_id": session.id,
            "source_document_id": document.id,
            "instruction": "How many rows in this workbook?",
        },
    )
    assert response.status_code == 200 and response.json()["response"] == '{"count":8}'
    phase4_turn.execute.assert_awaited_once()


def test_deterministic_operation_wins_before_codegen(document_api, monkeypatch, db_session):
    from app.services.document import phase4_turn
    from app.services.document.document_intent_service import DocumentIntentService
    from app.services.document.document_operation_registry import DocumentOperation

    _, session, document, _ = document_api
    monkeypatch.setattr(settings, "ENABLE_SANDBOX_EXECUTION", True)
    setattr(document, "filename", "data.xlsx")
    db_session.commit()
    execute_mock = AsyncMock()
    monkeypatch.setattr(phase4_turn, "execute", execute_mock)
    monkeypatch.setattr(
        DocumentIntentService,
        "resolve_plan",
        lambda *args, **kwargs: SimpleNamespace(
            steps=[SimpleNamespace(operation=DocumentOperation.FORMAT_WORKBOOK)],
            needs_clarification=False,
        ),
    )
    result = asyncio.run(
        phase4_turn.prepare_turn(db_session, 1, int(session.id), "Format this workbook")
    )
    assert result is None
    execute_mock.assert_not_called()
