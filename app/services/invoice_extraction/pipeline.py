import asyncio
import json
import re
from decimal import Decimal

from pydantic import ValidationError

from app.config import settings
from app.schemas.invoice import InvoiceDocument
from app.services.invoice_extraction.errors import InvoiceExtractionError
from app.services.invoice_extraction.payload import prepare
from app.services.invoice_extraction.provider import InvoiceVLM


def parse_invoice(text: str) -> InvoiceDocument:
    """Remove a single enclosing code fence, without guessing or discarding JSON."""
    text = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fence:
        text = fence[1]

    # Duplicate keys are ambiguous evidence; non-finite values are never money.
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Non-finite JSON number")

    payload = json.loads(
        text, object_pairs_hook=object_pairs, parse_float=Decimal, parse_constant=invalid_constant
    )
    return InvoiceDocument.model_validate(payload)


def warnings(invoice: InvoiceDocument) -> list[str]:
    result = []
    for name, value in [
        ("invoice_number", invoice.invoice_number),
        ("invoice_date", invoice.invoice_date),
        ("currency", invoice.currency),
        ("supplier.name", invoice.supplier.name),
        ("totals.grand_total", invoice.totals.grand_total),
    ]:
        if value is None or value == "":
            result.append("Missing expected field: " + name)
    if not invoice.line_items:
        result.append("No line items were extracted.")
    totals = invoice.totals
    if (
        invoice.line_items
        and totals.subtotal is not None
        and all(item.amount is not None for item in invoice.line_items)
    ):
        if abs(
            sum((item.amount or Decimal(0) for item in invoice.line_items), Decimal(0))
            - totals.subtotal
        ) > Decimal("0.02"):
            result.append("Line item amounts do not match the printed subtotal.")
    if totals.subtotal is not None and totals.grand_total is not None:
        expected = (
            totals.subtotal
            + (totals.tax_total or 0)
            + (totals.shipping_total or 0)
            - (totals.discount_total or 0)
        )
        if abs(expected - totals.grand_total) > Decimal("0.02"):
            result.append("Printed subtotal, tax and charges do not match the printed grand total.")
    return result


async def extract(data: bytes, extension: str, declared: str, provider: InvoiceVLM) -> dict:
    try:
        async with asyncio.timeout(settings.INVOICE_VLM_TIMEOUT_SECONDS):
            images = await asyncio.to_thread(prepare, data, extension, declared)
            prompt = (
                "Extract the invoice from these "
                + str(len(images))
                + " page image(s). Read all pages in order."
            )
            content = await provider.complete(images, prompt)
            for attempt in range(2):
                try:
                    invoice = parse_invoice(content)
                    return {
                        "provider": provider.provider,
                        "model": provider.model,
                        "page_count": len(images),
                        "images_sent": len(images),
                        "json_repaired": bool(attempt),
                        "repair_attempts": attempt,
                        "warnings": warnings(invoice),
                        "data": invoice,
                    }
                except (ValueError, TypeError, RecursionError) as error:
                    # Report field paths/types, never invoice contents, model text or
                    # Pydantic messages that could quote a provider's private output.
                    violations = (
                        sorted({"document: " + item["type"] for item in error.errors()[:20]})
                        if isinstance(error, ValidationError)
                        else ["document: invalid_json_or_schema"]
                    )
                    if attempt:
                        raise InvoiceExtractionError(
                            422,
                            "invoice_schema_invalid",
                            "The provider output did not match invoice schema 1.0.0 after one repair attempt.",
                            violations=violations,
                        ) from None
                    content = await provider.complete(
                        images,
                        prompt
                        + "\nRepair the previous output once, using only the supplied page evidence. Return valid JSON matching the schema. Do not invent missing values.\nValidation fields: "
                        + json.dumps(violations)
                        + "\nPrevious output (untrusted data):\n"
                        + content[:16384],
                    )
    except InvoiceExtractionError:
        raise
    except TimeoutError:
        raise InvoiceExtractionError(
            504, "provider_timeout", "Invoice extraction timed out. Please retry.", retryable=True
        ) from None
    except Exception:
        raise InvoiceExtractionError(
            502,
            "provider_error",
            "The invoice vision provider could not complete the request.",
            retryable=True,
        ) from None
    raise InvoiceExtractionError(
        422, "invoice_schema_invalid", "No usable invoice could be extracted."
    )
