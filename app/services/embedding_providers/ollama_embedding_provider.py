import logging
from urllib.parse import urlsplit, urlunsplit

import requests

from app.config import settings
from app.services.embedding_providers.base import EmbeddingModelUnavailableError, EmbeddingProvider

logger = logging.getLogger(__name__)


def _ollama_embed_url() -> str:
    parts = urlsplit(settings.OLLAMA_URL)
    return urlunsplit((parts.scheme, parts.netloc, "/api/embed", "", ""))


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Local, private embeddings via a self-hosted Ollama server.

    Requires an embedding model (OLLAMA_EMBEDDING_MODEL, default
    "nomic-embed-text") to be pulled separately from the chat/vision models -
    this is a new operational requirement, not something the app pulls itself.
    """

    def embed(self, texts: list[str], *, input_type: str = "passage") -> list[list[float]]:
        del input_type  # Ollama's embed API has no query/passage distinction.
        try:
            response = requests.post(
                _ollama_embed_url(),
                json={"model": settings.OLLAMA_EMBEDDING_MODEL, "input": texts},
                timeout=(settings.OLLAMA_CONNECT_TIMEOUT_SECONDS, settings.CHROMA_TIMEOUT_SECONDS),
            )
            response.raise_for_status()
        except requests.Timeout as error:
            logger.warning("rag.embedding_model_timeout", extra={"provider": "ollama"})
            raise EmbeddingModelUnavailableError("Ollama embedding request timed out.") from error
        except requests.RequestException as error:
            logger.warning("rag.embedding_model_unavailable", extra={"provider": "ollama"})
            raise EmbeddingModelUnavailableError(
                "Ollama embedding model unavailable: configured model "
                f"'{settings.OLLAMA_EMBEDDING_MODEL}'. Confirm Ollama is running "
                "and the model is installed."
            ) from error

        try:
            embeddings = response.json()["embeddings"]
            if not isinstance(embeddings, list) or len(embeddings) != len(texts):
                raise ValueError("unexpected embeddings shape")
            return embeddings
        except (KeyError, TypeError, ValueError) as error:
            raise EmbeddingModelUnavailableError(
                "Ollama embedding response was not in the expected shape."
            ) from error
