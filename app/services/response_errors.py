"""Keep model/server configuration details out of chat responses."""

import re

from app.services.image_parser_service import VisionModelUnavailableError

_PROVIDER_DETAIL = re.compile(
    r"ollama|nvidia|qwen|api[_ -]?key|configured model|model unavailable|pull the model",
    re.I,
)


def public_model_error(error: Exception) -> str:
    message = str(error)
    if not _PROVIDER_DETAIL.search(message):
        return message
    task = (
        "Image analysis"
        if isinstance(error, VisionModelUnavailableError)
        else "The response service"
    )
    return f"{task} is temporarily unavailable. Please retry your message."
