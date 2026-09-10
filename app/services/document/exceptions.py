class InvalidDocumentError(ValueError):
    """The supplied bytes are empty, malformed, or unsupported."""


class DocumentProcessingUnavailableError(RuntimeError):
    """A required deterministic document-processing dependency is unavailable."""
