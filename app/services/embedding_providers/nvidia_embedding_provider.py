import logging

import requests

from app.config import settings
from app.services.embedding_providers.base import EmbeddingModelUnavailableError, EmbeddingProvider
from app.services.http_retry import post_with_retry

logger = logging.getLogger(__name__)
_HTTP_SESSION = requests.Session()


class NvidiaEmbeddingProvider(EmbeddingProvider):
    """Hosted embeddings via NVIDIA's NIM API catalog (build.nvidia.com)."""

    def embed(self, texts: list[str], *, input_type: str = "passage") -> list[list[float]]:
        if not settings.NVIDIA_API_KEY:
            raise EmbeddingModelUnavailableError("NVIDIA_API_KEY is not configured.")

        try:
            response = post_with_retry(
                _HTTP_SESSION,
                f"{settings.NVIDIA_API_BASE_URL}/embeddings",
                headers={
                    "Authorization": f"Bearer {settings.NVIDIA_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.EMBEDDING_MODEL,
                    "input": texts,
                    "input_type": input_type,
                },
                timeout=(
                    settings.NVIDIA_CHAT_CONNECT_TIMEOUT_SECONDS,
                    settings.CHROMA_TIMEOUT_SECONDS,
                ),
            )
            response.raise_for_status()
        except requests.Timeout as error:
            logger.warning("rag.embedding_model_timeout", extra={"provider": "nvidia"})
            raise EmbeddingModelUnavailableError("NVIDIA embedding request timed out.") from error
        except requests.RequestException as error:
            logger.warning("rag.embedding_model_unavailable", extra={"provider": "nvidia"})
            raise EmbeddingModelUnavailableError(
                "NVIDIA embedding API unavailable: configured model "
                f"'{settings.EMBEDDING_MODEL}'. Confirm NVIDIA_API_KEY is valid."
            ) from error

        try:
            data = response.json()["data"]
            ordered = sorted(data, key=lambda item: item["index"])
            return [item["embedding"] for item in ordered]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise EmbeddingModelUnavailableError(
                "NVIDIA embedding response was not in the expected shape."
            ) from error
