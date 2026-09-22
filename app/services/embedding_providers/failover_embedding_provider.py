import logging

from app.services.embedding_providers.base import EmbeddingModelUnavailableError, EmbeddingProvider
from app.services.embedding_providers.nvidia_embedding_provider import NvidiaEmbeddingProvider
from app.services.embedding_providers.ollama_embedding_provider import OllamaEmbeddingProvider

logger = logging.getLogger(__name__)


class FailoverEmbeddingProvider(EmbeddingProvider):
    """Try NVIDIA once, then retry the same batch once with Ollama."""

    def __init__(
        self,
        nvidia: EmbeddingProvider | None = None,
        ollama: EmbeddingProvider | None = None,
    ) -> None:
        self.nvidia = nvidia or NvidiaEmbeddingProvider()
        self.ollama = ollama or OllamaEmbeddingProvider()

    def embed(self, texts: list[str], *, input_type: str = "passage") -> list[list[float]]:
        try:
            return self.nvidia.embed(texts, input_type=input_type)
        except EmbeddingModelUnavailableError:
            logger.warning("rag.embedding_nvidia_fallback_to_ollama")
            return self.ollama.embed(texts, input_type=input_type)
