"""Cheap container validation before queueing expensive extraction."""

import zipfile
from io import BytesIO

from fastapi import HTTPException


def validate_document_container(data: bytes, extension: str):
    try:
        if extension in {".xlsx", ".docx", ".pptx"}:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                names = archive.namelist()
                required = {
                    ".xlsx": "xl/workbook.xml",
                    ".docx": "word/document.xml",
                    ".pptx": "ppt/presentation.xml",
                }[extension]
                if (
                    required not in names
                    or sum(i.file_size for i in archive.infolist()) > 100 * 1024 * 1024
                ):
                    raise ValueError("Invalid office document")
        elif extension == ".pdf" and not data.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF")
        elif extension in {".txt", ".csv", ".md", ".markdown"}:
            data.decode("utf-8-sig")
    except (ValueError, zipfile.BadZipFile, UnicodeError) as error:
        raise HTTPException(
            422, "The file content does not match its type or is too large to extract."
        ) from error
