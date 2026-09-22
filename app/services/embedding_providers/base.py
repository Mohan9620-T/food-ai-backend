from abc import ABC, abstractmethod


class EmbeddingModelUnavailableError(RuntimeError):
    """Raised on network/timeout/HTTP failure, or an unparseable provider response."""


class EmbeddingProvider(ABC):
    """
    An embedding provider turns a batch of texts into one vector per text, in
    the same order. Swapping EMBEDDING_PROVIDER in .env swaps the concrete
    implementation used at runtime — callers never talk to Ollama or NVIDIA
    directly, only to this interface. Mirrors the VisionProvider ABC pattern
    in app.services.vision_providers.base.
    """

    @abstractmethod
    def embed(self, texts: list[str], *, input_type: str = "passage") -> list[list[float]]:
        """Return one embedding vector per input text, in the same order.

        Args:
            texts: The texts to embed. Never empty.
            input_type: "query" for a search query, "passage" for indexed
                document content. Asymmetric embedding models use this to
                embed queries and passages differently; providers that don't
                support the distinction may ignore it.

        Raises:
            EmbeddingModelUnavailableError: on timeout or the provider being
                unreachable/unconfigured.
        """
        raise NotImplementedError
