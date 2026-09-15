class InvalidDocumentError(ValueError):
    """The supplied bytes are empty, malformed, or unsupported."""


class DocumentLocatorError(InvalidDocumentError):
    """A requested edit target did not resolve to exactly one document region."""


class UnsupportedDocumentModificationError(InvalidDocumentError):
    """The requested document edit cannot be performed reliably."""


class DocumentChangeVerificationError(InvalidDocumentError):
    """A generated modification changed more than the requested region."""


class DocumentProcessingUnavailableError(RuntimeError):
    """A required deterministic document-processing dependency is unavailable."""
