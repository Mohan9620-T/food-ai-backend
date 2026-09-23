import logging
from urllib.parse import urlsplit

import requests

from app.config import settings

logger = logging.getLogger(__name__)

_session = requests.Session()


class WebSearchUnavailableError(Exception):
    pass


def search(query: str) -> list[dict] | None:
    """Return up to WEB_SEARCH_MAX_RESULTS {title, url, content} results, or None.

    None means search was skipped or failed - callers must fall back to
    answering without web results, never block or fail the chat response.
    """
    if not settings.ENABLE_WEB_SEARCH or not settings.TAVILY_API_KEY:
        return None
    try:
        response = _session.post(
            f"{settings.TAVILY_API_BASE_URL}/search",
            headers={
                "Authorization": f"Bearer {settings.TAVILY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "query": query[:1000],
                "max_results": max(1, min(settings.WEB_SEARCH_MAX_RESULTS, 5)),
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
            },
            timeout=(5, settings.TAVILY_TIMEOUT_SECONDS),
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise WebSearchUnavailableError("malformed Tavily response")
        results = data.get("results")
        if not isinstance(results, list):
            raise WebSearchUnavailableError("malformed Tavily response")
        return [
            {
                "title": str(item.get("title") or "")[:200],
                "url": str(item.get("url") or ""),
                "content": str(item.get("content") or "")[:4000],
            }
            for item in results[: max(1, min(settings.WEB_SEARCH_MAX_RESULTS, 5))]
            if isinstance(item, dict)
            and isinstance(item.get("url"), str)
            and urlsplit(item["url"]).scheme in {"http", "https"}
            and urlsplit(item["url"]).hostname
        ]
    except (requests.RequestException, WebSearchUnavailableError, ValueError, TypeError) as error:
        logger.warning("chat.web_search_failed", extra={"reason": type(error).__name__})
        return None
