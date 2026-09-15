from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader, PdfWriter

from app.services.document.exceptions import (
    DocumentLocatorError,
    UnsupportedDocumentModificationError,
)
from app.services.document.extraction_models import DocumentEdit, DocumentEditAction


class PdfModifier:
    """Perform structural PDF page operations; arbitrary visual edits are rejected."""

    _PAGE_ACTIONS = {
        DocumentEditAction.REMOVE_PDF_PAGES,
        DocumentEditAction.EXTRACT_PDF_PAGES,
        DocumentEditAction.REORDER_PDF_PAGES,
    }

    def modify(self, file_data: bytes, edits: tuple[DocumentEdit, ...]) -> bytes:
        current = file_data
        for edit in edits:
            if edit.action not in self._PAGE_ACTIONS:
                raise UnsupportedDocumentModificationError(
                    "Arbitrary visual PDF editing is not supported reliably. "
                    "A reconstruction workflow (PDF to Word, edit, then PDF) can be used once "
                    "the required conversion path is available."
                )
            current = self._apply_page_edit(current, edit)
        return current

    def merge(self, files: tuple[bytes, ...]) -> bytes:
        if len(files) < 2:
            raise DocumentLocatorError("PDF merging requires at least two PDF files.")
        writer = PdfWriter()
        for file_data in files:
            reader = PdfReader(BytesIO(file_data))
            for page in reader.pages:
                writer.add_page(page)
        return self._write(writer)

    def _apply_page_edit(self, file_data: bytes, edit: DocumentEdit) -> bytes:
        reader = PdfReader(BytesIO(file_data))
        page_count = len(reader.pages)
        pages = edit.locator.pages
        if not pages:
            raise DocumentLocatorError("A PDF page operation requires one or more page numbers.")
        if len(set(pages)) != len(pages):
            raise DocumentLocatorError("Each PDF page number must be listed only once.")
        invalid = [page for page in pages if page < 1 or page > page_count]
        if invalid:
            raise DocumentLocatorError(
                f"PDF page {invalid[0]} does not exist; this document has {page_count} pages."
            )
        requested_indexes = [page - 1 for page in pages]
        if edit.action == DocumentEditAction.REMOVE_PDF_PAGES:
            selected = [index for index in range(page_count) if index not in requested_indexes]
            if not selected:
                raise UnsupportedDocumentModificationError(
                    "Removing every page would create an invalid empty PDF."
                )
        elif edit.action == DocumentEditAction.EXTRACT_PDF_PAGES:
            selected = requested_indexes
        else:
            if sorted(requested_indexes) != list(range(page_count)):
                raise DocumentLocatorError(
                    "PDF reordering must list every page exactly once in the desired order."
                )
            selected = requested_indexes
        writer = PdfWriter()
        for index in selected:
            writer.add_page(reader.pages[index])
        return self._write(writer)

    @staticmethod
    def _write(writer: PdfWriter) -> bytes:
        output = BytesIO()
        writer.write(output)
        return output.getvalue()
