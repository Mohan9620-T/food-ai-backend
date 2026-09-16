"""Locate the optional OCR executable without requiring a terminal PATH refresh."""

import os
import shutil
from pathlib import Path

import pytesseract

from app.config import settings


def configure_tesseract() -> None:
    if settings.TESSERACT_BINARY:
        pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_BINARY
        return
    if pytesseract.pytesseract.tesseract_cmd != "tesseract" or shutil.which("tesseract"):
        return
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Tesseract-OCR/tesseract.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Tesseract-OCR/tesseract.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                pytesseract.pytesseract.tesseract_cmd = str(candidate)
                return
