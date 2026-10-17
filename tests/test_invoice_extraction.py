"""Invoice contract and failure paths. Every provider/network call uses a fake."""

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from io import BytesIO
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError
from pypdf import PdfWriter

from app.config import settings
from app.main import app
from app.models.user import User
from app.schemas.invoice import InvoiceDocument
from app.services.invoice_extraction import payload, pipeline
from app.services.invoice_extraction import provider as providers
from app.services.invoice_extraction.errors import InvoiceExtractionError
from app.utils.security import create_access_token


def invoice():
    return {
        "invoice_number": "INV-42",
        "invoice_date": "2026-10-17",
        "currency": "SGD",
        "supplier": {"name": "Supplier"},
        "line_items": [
            {"description": "Lunch", "quantity": "10", "unit_price": "12.00", "amount": "120.00"}
        ],
        "totals": {"subtotal": "120.00", "tax_total": "10.80", "grand_total": "130.80"},
    }


class FakeVLM:
    provider = "test"
    model = "fake-vision"

    def __init__(self):
        self.outputs = [json.dumps(invoice())]
        self.calls = []
        self.healthy = True

    async def complete(self, images, prompt):
        self.calls.append((images, prompt))
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def health(self):
        return self.healthy


@pytest.fixture
def invoices(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_INVOICE_EXTRACTION", True)
    monkeypatch.setattr(settings, "INVOICE_MAX_PAGES", 5)
    monkeypatch.setattr(settings, "INVOICE_MAX_UPLOAD_BYTES", 15 * 1024 * 1024)
    db_session.add(
        User(id=301, fullname="Invoice owner", email="invoice@example.com", password="unused")
    )
    db_session.commit()
    fake = FakeVLM()
    app.dependency_overrides[providers.get_invoice_vlm] = lambda: fake
    headers = {"Authorization": "Bearer " + create_access_token({"sub": "301", "auth_version": 0})}
    return client, headers, fake


def pdf(pages=1, encrypted=False):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=300, height=400)
    if encrypted:
        writer.encrypt("test-password")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_invoice_success_and_provider_metadata(invoices, valid_png_bytes):
    client, headers, fake = invoices
    response = client.post(
        "/documents/extract-invoice",
        headers=headers,
        files={"file": ("invoice.png", valid_png_bytes, "image/png")},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["schema_version"] == "1.0.0" and result["request_id"]
    assert result["data"]["totals"]["grand_total"] == "130.80"
    assert result["page_count"] == result["images_sent"] == 1
    assert result["warnings"] == [] and not result["json_repaired"]
    assert response.headers["cache-control"] == "no-store"
    assert len(fake.calls) == 1
    assert base64.b64decode(fake.calls[0][0][0]).startswith(b"\xff\xd8")
    meta = client.get("/documents/vlm/provider", headers=headers).json()
    assert meta["provider"] == "test" and meta["max_pages"] == 5 and "key" not in meta
    assert client.get("/documents/vlm/health", headers=headers).json()["healthy"]
    fake.healthy = False
    assert client.get("/documents/vlm/health", headers=headers).status_code == 503


def test_auth_and_disabled_routes(invoices, monkeypatch, valid_png_bytes):
    client, headers, fake = invoices
    assert client.get("/documents/vlm/provider").status_code == 401
    assert (
        client.post(
            "/documents/extract-invoice",
            files={"file": ("invoice.png", valid_png_bytes, "image/png")},
        ).status_code
        == 401
    )
    monkeypatch.setattr(settings, "ENABLE_INVOICE_EXTRACTION", False)
    for path in ("/documents/vlm/provider", "/documents/vlm/health"):
        assert client.get(path, headers=headers).status_code == 404
    assert (
        client.post(
            "/documents/extract-invoice",
            headers=headers,
            files={"file": ("invoice.png", valid_png_bytes, "image/png")},
        ).status_code
        == 404
    )
    assert not fake.calls


@pytest.mark.parametrize(
    "first",
    [
        "invalid JSON",
        '{"invoice_number":',
        '{"currency":"dollars","invoice_number":"42"}',
        '{"totals":{"grand_total":"NaN"}}',
        "{}",
        '{"private-secret":"never echo this"}',
    ],
)
def test_single_repair_then_validated(invoices, valid_png_bytes, first):
    client, headers, fake = invoices
    fake.outputs = [first, json.dumps(invoice())]
    response = client.post(
        "/documents/extract-invoice",
        headers=headers,
        files={"file": ("invoice.png", valid_png_bytes, "image/png")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["repair_attempts"] == 1 and response.json()["json_repaired"]
    assert len(fake.calls) == 2 and fake.calls[0][0] == fake.calls[1][0]


def test_schema_failure_does_not_disclose_model_text(invoices, valid_png_bytes):
    client, headers, fake = invoices
    fake.outputs = ['{"private-secret":"private-value"}'] * 2
    response = client.post(
        "/documents/extract-invoice",
        headers=headers,
        files={"file": ("invoice.png", valid_png_bytes, "image/png")},
    )
    assert response.status_code == 422
    result = response.json()
    assert result["error"]["code"] == "invoice_schema_invalid" and result["request_id"]
    assert "private-secret" not in response.text and "private-value" not in response.text
    assert len(fake.calls) == 2


@pytest.mark.parametrize(
    "error,status,code",
    [
        (RuntimeError("private-token"), 502, "provider_error"),
        (TimeoutError("private-timeout"), 504, "provider_timeout"),
        (
            InvoiceExtractionError(502, "provider_error", "Provider unavailable"),
            502,
            "provider_error",
        ),
    ],
)
def test_typed_provider_failures(invoices, valid_png_bytes, error, status, code):
    client, headers, fake = invoices
    fake.outputs = [error]
    response = client.post(
        "/documents/extract-invoice",
        headers=headers,
        files={"file": ("invoice.png", valid_png_bytes, "image/png")},
    )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "private" not in response.text and len(fake.calls) == 1


@pytest.mark.parametrize("pages,limit,status", [(2, 5, 200), (2, 1, 422), (0, 5, 422)])
def test_pdf_pages_are_rendered_or_rejected_before_provider(
    invoices, monkeypatch, pages, limit, status
):
    client, headers, fake = invoices
    monkeypatch.setattr(settings, "INVOICE_MAX_PAGES", limit)
    response = client.post(
        "/documents/extract-invoice",
        headers=headers,
        files={"file": ("invoice.pdf", pdf(pages), "application/pdf")},
    )
    assert response.status_code == status, response.text
    if status == 200:
        assert response.json()["page_count"] == len(fake.calls[0][0]) == pages
    else:
        assert fake.calls == []


def test_encrypted_pdf_and_oversized_upload(invoices, monkeypatch, valid_png_bytes):
    client, headers, fake = invoices
    response = client.post(
        "/documents/extract-invoice",
        headers=headers,
        files={"file": ("invoice.pdf", pdf(encrypted=True), "application/pdf")},
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "encrypted_document"
    monkeypatch.setattr(settings, "INVOICE_MAX_UPLOAD_BYTES", 10)
    response = client.post(
        "/documents/extract-invoice",
        headers=headers,
        files={"file": ("invoice.png", valid_png_bytes, "image/png")},
    )
    assert response.status_code == 413
    assert not fake.calls


@pytest.mark.parametrize(
    "filename,data,mime,status",
    [
        ("invoice.txt", b"text", "text/plain", 415),
        ("invoice.pdf", b"not a PDF", "application/pdf", 422),
        ("invoice.png", b"fake image", "image/png", 422),
        ("invoice.png", b"", "image/png", 422),
        ("invoice.png", b"fake", "application/pdf", 415),
    ],
)
def test_upload_validation(invoices, filename, data, mime, status):
    client, headers, fake = invoices
    response = client.post(
        "/documents/extract-invoice", headers=headers, files={"file": (filename, data, mime)}
    )
    assert response.status_code == status
    assert not fake.calls


def test_animated_images_are_not_silently_truncated():
    image1 = Image.new("RGB", (2, 2), "red")
    image2 = Image.new("RGB", (2, 2), "blue")
    output = BytesIO()
    image1.save(output, "GIF", save_all=True, append_images=[image2])
    with pytest.raises(InvoiceExtractionError) as error:
        payload.prepare(output.getvalue(), ".gif", "image/gif")
    assert error.value.code == "animated_image"


@pytest.mark.parametrize(
    "raw",
    [
        "{}",
        "[]",
        '{"totals":{"grand_total":NaN}}',
        '{"invoice_number":"1","invoice_number":"2"}',
        '{"invoice_number":"1","currency":"$"}',
        '{"invoice_number":"1","invoice_date":"2026-02-31"}',
    ],
)
def test_schema_rejects_unusable_or_ambiguous_values(raw):
    with pytest.raises((ValueError, ValidationError)):
        pipeline.parse_invoice(raw)


def test_partial_invoice_and_money_warnings():
    partial = pipeline.parse_invoice('```json\n{"supplier":{"name":"Supplier"}}\n```')
    assert partial.invoice_number is None and partial.totals.grand_total is None
    blank = pipeline.parse_invoice(
        '{"supplier":{"name":"Supplier"},"invoice_number":"  ","currency":""}'
    )
    assert blank.invoice_number is None and blank.currency is None
    assert "Missing expected field: invoice_number" in pipeline.warnings(partial)
    data = invoice()
    data["totals"]["subtotal"] = "20.00"
    validated = InvoiceDocument.model_validate(data)
    assert validated.totals.grand_total == Decimal("130.80")
    assert len(pipeline.warnings(validated)) == 2


@pytest.mark.parametrize("selected", ["ollama", "nvidia"])
def test_provider_adapter_payload_and_health(monkeypatch, selected):
    monkeypatch.setattr(settings, "INVOICE_VLM_PROVIDER", selected)
    monkeypatch.setattr(settings, "INVOICE_VLM_MODEL", "vision-test")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "fake-key")
    calls = []

    async def handle(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"models": [{"name": "vision-test:latest"}], "data": [{"id": "vision-test"}]},
            )
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "message": {"content": json.dumps(invoice())},
                "choices": [{"message": {"content": json.dumps(invoice())}}],
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle)),
    )

    @asynccontextmanager
    async def context(timeout):
        async with real_client(transport=httpx.MockTransport(handle)) as client:
            yield client

    monkeypatch.setattr(providers, "client_context", context)

    async def run():
        provider = providers.get_invoice_vlm()
        assert await provider.health()
        assert (
            pipeline.parse_invoice(
                await provider.complete(["encoded-page"], "Read invoice")
            ).invoice_number
            == "INV-42"
        )

    asyncio.run(run())
    user = calls[0]["messages"][1]
    if selected == "ollama":
        assert (
            user["images"] == ["encoded-page"] and calls[0]["format"]["title"] == "InvoiceDocument"
        )
    else:
        assert user["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,encoded-page"


@pytest.mark.parametrize(
    "error,status", [(httpx.ReadTimeout("private"), 504), (httpx.ConnectError("private"), 502)]
)
def test_provider_transport_errors_are_safe(monkeypatch, error, status):
    monkeypatch.setattr(settings, "INVOICE_VLM_PROVIDER", "ollama")
    real_client = httpx.AsyncClient

    def handle(request):
        raise error

    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle)),
    )

    async def run():
        provider = providers.get_invoice_vlm()
        assert not await provider.health()
        with pytest.raises(InvoiceExtractionError) as result:
            await provider.complete(["image"], "Read invoice")
        assert result.value.status == status and "private" not in str(result.value)

    asyncio.run(run())


def test_configured_provider_failure_is_typed(invoices, monkeypatch, valid_png_bytes):
    client, headers, _ = invoices
    app.dependency_overrides.pop(providers.get_invoice_vlm)
    monkeypatch.setattr(settings, "INVOICE_VLM_PROVIDER", "unsupported")
    response = client.post(
        "/documents/extract-invoice",
        headers=headers,
        files={"file": ("invoice.png", valid_png_bytes, "image/png")},
    )
    assert (
        response.status_code == 502
        and response.json()["error"]["code"] == "provider_not_configured"
    )


def test_total_deadline_includes_provider_and_repair(invoices, monkeypatch, valid_png_bytes):
    client, headers, fake = invoices
    monkeypatch.setattr(settings, "INVOICE_VLM_TIMEOUT_SECONDS", 0.02)

    async def slow(*args):
        await asyncio.sleep(0.2)
        return json.dumps(invoice())

    fake.complete = slow
    response = client.post(
        "/documents/extract-invoice",
        headers=headers,
        files={"file": ("invoice.png", valid_png_bytes, "image/png")},
    )
    assert response.status_code == 504


def test_health_failure_is_safe(invoices):
    client, headers, fake = invoices
    fake.health = AsyncMock(side_effect=RuntimeError("private provider body"))
    response = client.get("/documents/vlm/health", headers=headers)
    assert response.status_code == 503 and "private" not in response.text


def test_missing_upload_returns_typed_error(invoices):
    client, headers, fake = invoices
    response = client.post("/documents/extract-invoice", headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_upload"
    assert not fake.calls


def test_invoice_accepts_owned_api_key_and_rejects_revoked_key(
    invoices, monkeypatch, valid_png_bytes
):
    client, headers, fake = invoices
    monkeypatch.setattr(settings, "ENABLE_API_KEYS", True)
    created = client.post("/users/keys", headers=headers, json={"name": "Invoice importer"}).json()
    key_headers = {"X-API-Key": created["key"]}
    files = {"file": ("invoice.png", valid_png_bytes, "image/png")}
    assert (
        client.post("/documents/extract-invoice", headers=key_headers, files=files).status_code
        == 200
    )
    assert client.delete(f"/users/keys/{created['id']}", headers=headers).status_code == 200
    assert (
        client.post("/documents/extract-invoice", headers=key_headers, files=files).status_code
        == 401
    )
    assert len(fake.calls) == 1


def test_busy_local_vision_can_be_cancelled_without_stealing_slot():
    from app.services.vision_runtime import vision_inference_slot

    async def waiting():
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.01):
                async with providers.local_vision_slot():
                    pytest.fail("Busy vision slot must not be entered")

    with vision_inference_slot():
        asyncio.run(waiting())

    async def available():
        async with providers.local_vision_slot():
            pass

    asyncio.run(available())


def test_provider_protocol_failure_does_not_reveal_body(monkeypatch):
    monkeypatch.setattr(settings, "INVOICE_VLM_PROVIDER", "nvidia")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "fake-provider-key")
    monkeypatch.setattr(settings, "INVOICE_VLM_MODEL", "nemotron-3-nano-omni-test")
    real_client = httpx.AsyncClient
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"private-provider-response": "private-value"})

    @asynccontextmanager
    async def context(timeout):
        async with real_client(transport=httpx.MockTransport(handle)) as client:
            yield client

    monkeypatch.setattr(providers, "client_context", context)

    async def run():
        with pytest.raises(InvoiceExtractionError) as error:
            await providers.get_invoice_vlm().complete(["page"], "Read invoice")
        assert error.value.status == 502 and "private" not in str(error.value)

    asyncio.run(run())
    assert bodies[0]["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize("status,can_retry", [(400, False), (429, True), (503, True)])
def test_provider_status_errors_have_safe_retry_advice(monkeypatch, status, can_retry):
    monkeypatch.setattr(settings, "INVOICE_VLM_PROVIDER", "ollama")
    real_client = httpx.AsyncClient

    def handle(request):
        return httpx.Response(status, json={"private-provider-body": "not for clients"})

    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle)),
    )

    async def run():
        with pytest.raises(InvoiceExtractionError) as error:
            await providers.get_invoice_vlm().complete(["image"], "Read invoice")
        assert error.value.status == 502 and error.value.retryable is can_retry
        assert "private-provider-body" not in str(error.value)

    asyncio.run(run())


def test_invoice_error_reuses_request_id(invoices, monkeypatch, valid_png_bytes):
    client, headers, fake = invoices
    monkeypatch.setattr(settings, "ENABLE_REQUEST_IDS", True)
    fake.outputs = ["invalid", "invalid"]
    response = client.post(
        "/documents/extract-invoice",
        headers={**headers, "X-Request-ID": "invoice-test-001"},
        files={"file": ("invoice.png", valid_png_bytes, "image/png")},
    )
    assert response.status_code == 422
    assert response.json()["request_id"] == response.headers["x-request-id"] == "invoice-test-001"
