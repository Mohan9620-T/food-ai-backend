import re
from dataclasses import dataclass

from app.services.document.extraction_models import (
    DocumentBlockType,
    DocumentLocation,
    ExtractedDocument,
)


@dataclass(frozen=True)
class SemanticChunk:
    """One retrieval-sized slice of a document, for embedding and indexing.

    Distinct from ChatDocumentService._document_chunks, which packs blocks
    into much larger (~30k char) chunks for map-reduce summarization. These
    chunks are small and overlapping, sized for semantic similarity search.
    """

    text: str
    block_type: str
    location: DocumentLocation
    chunk_index: int


def _split_with_overlap(text: str, chunk_chars: int, overlap_chars: int) -> list[str]:
    """Slide a window over text, snapping to whitespace where possible."""
    if len(text) <= chunk_chars:
        return [text.strip()] if text.strip() else []
    step = max(1, chunk_chars - overlap_chars)
    pieces: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + chunk_chars, length)
        if end < length:
            boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= length:
            break
        start += step
    return pieces


def semantic_chunks(
    extracted: ExtractedDocument,
    *,
    chunk_chars: int = 800,
    overlap_chars: int = 100,
) -> list[SemanticChunk]:
    """Split an already-extracted document into small, overlapping chunks.

    Reuses extracted.blocks (the same structural units ChatDocumentService's
    own map-reduce chunker builds from) rather than re-parsing file bytes, so
    this never duplicates the deterministic extraction pipeline.
    """
    units: list[tuple[str, DocumentBlockType, DocumentLocation]] = []
    for block in extracted.blocks:
        cell_text = "\n".join("\t".join(row) for row in block.cells)
        body = "\n".join(part for part in (block.text, cell_text) if part)
        if body.strip():
            units.append((body, block.type, block.location))

    if not units:
        units = [
            (part, DocumentBlockType.PARAGRAPH, DocumentLocation())
            for part in re.split(r"(?<=\n)\s*\n", extracted.text)
            if part.strip()
        ]

    chunks: list[SemanticChunk] = []
    for body, block_type, location in units:
        for piece in _split_with_overlap(body, chunk_chars, overlap_chars):
            chunks.append(
                SemanticChunk(
                    text=piece,
                    block_type=block_type.value,
                    location=location,
                    chunk_index=len(chunks),
                )
            )
    return chunks
