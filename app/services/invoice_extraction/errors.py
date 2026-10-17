class InvoiceExtractionError(Exception):
    """Only these fixed messages may cross the API boundary."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        violations: list[str] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.violations = violations or []
