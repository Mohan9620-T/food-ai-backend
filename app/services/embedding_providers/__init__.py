from app.config import settings
from app.services.embedding_providers.base import EmbeddingModelUnavailableError, EmbeddingProvider
from app.services.embedding_providers.failover_embedding_provider import FailoverEmbeddingProvider
from app.services.embedding_providers.ollama_embedding_provider import OllamaEmbeddingProvider


def get_embedding_provider() -> EmbeddingProvider:
    """Return the configured embedding provider.

    EMBEDDING_PROVIDER="ollama" stays fully local (no NVIDIA call at all, for
    deployments that want embeddings kept private); anything else (default
    "nvidia") tries NVIDIA first with an Ollama fallback, mirroring
    app.services.vision_providers.get_vision_provider.
    """
    if settings.EMBEDDING_PROVIDER == "ollama":
        return OllamaEmbeddingProvider()
    return FailoverEmbeddingProvider()


__all__ = ["EmbeddingModelUnavailableError", "EmbeddingProvider", "get_embedding_provider"]
