import base64
import math
from contextlib import closing
from io import BytesIO

from PIL import Image
from pypdf import PdfReader
from pypdfium2 import PdfDocument

from app.config import settings
from app.services.image_validation import validate_image_content
from app.services.invoice_extraction.errors import InvoiceExtractionError
from app.services.vision_image_preprocessor import prepare_vision_image

TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def prepare(data: bytes, extension: str, declared: str) -> list[str]:
    expected = TYPES.get(extension)
    if expected is None or declared not in {expected, "", "application/octet-stream"}:
        raise InvoiceExtractionError(
            415,
            "unsupported_media_type",
            "Upload a PDF, PNG, JPEG, WebP or single-frame GIF invoice.",
        )
    if not data:
        raise InvoiceExtractionError(422, "invalid_document", "The uploaded document is empty.")
    if len(data) > settings.INVOICE_MAX_UPLOAD_BYTES:
        raise InvoiceExtractionError(
            413, "document_too_large", "The invoice exceeds the upload size limit."
        )
    try:
        if expected != "application/pdf":
            validate_image_content(data, expected)
            with Image.open(BytesIO(data)) as source:
                if getattr(source, "n_frames", 1) != 1:
                    raise InvoiceExtractionError(
                        422,
                        "animated_image",
                        "Upload a single-frame image or PDF so every page can be extracted.",
                    )
            jpeg = prepare_vision_image(data, max_dimension=1800, force_jpeg=True)
            return [base64.b64encode(jpeg).decode("ascii")]
        if not data.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF")
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise InvoiceExtractionError(
                422, "encrypted_document", "Remove the PDF password before uploading the invoice."
            )
        count = len(reader.pages)
        if count < 1:
            raise ValueError("Empty PDF")
        if count > settings.INVOICE_MAX_PAGES:
            raise InvoiceExtractionError(
                422, "page_limit_exceeded", "The invoice exceeds the configured page limit."
            )
        images = []
        with closing(PdfDocument(data)) as pdf:
            if len(pdf) != count:
                raise ValueError("Inconsistent page count")
            for index in range(count):
                with closing(pdf[index]) as page:
                    width, height = page.get_size()
                    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                        raise ValueError("Invalid page size")
                    scale = min(2, 1800 / max(width, height))
                    with closing(page.render(scale=scale)) as bitmap:
                        with closing(bitmap.to_pil()) as image:
                            with image.convert("RGB") as rgb:
                                output = BytesIO()
                                rgb.save(output, format="JPEG", quality=85)
                                images.append(base64.b64encode(output.getvalue()).decode("ascii"))
        return images
    except InvoiceExtractionError:
        raise
    except Exception:
        raise InvoiceExtractionError(
            422,
            "invalid_document",
            "The invoice cannot be read. Upload a valid, unencrypted PDF or image.",
        ) from None
