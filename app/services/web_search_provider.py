import json
import logging
import re
import time
from urllib.parse import urlencode, urlsplit

import requests

from app.config import settings
from app.services.web_page_reader import WebPageError, _fetch

logger = logging.getLogger(__name__)

_session = requests.Session()


class WebSearchUnavailableError(Exception):
    pass


def search(query: str) -> list[dict] | None:
    """Search Tavily when configured; otherwise try live Wikipedia introductions."""
    if not settings.ENABLE_WEB_SEARCH:
        return None
    if settings.TAVILY_API_KEY:
        results = _search_tavily(query)
        if results:
            return results
    return _search_wikipedia(query)


def _search_tavily(query: str) -> list[dict] | None:
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
            and isinstance(item.get("content"), str)
            and item["content"].strip()
            and urlsplit(item["url"]).scheme in {"http", "https"}
            and urlsplit(item["url"]).hostname
        ]
    except (requests.RequestException, WebSearchUnavailableError, ValueError, TypeError) as error:
        logger.warning("chat.web_search_failed", extra={"reason": type(error).__name__})
        return None


def _wikipedia_query(query: str) -> str:
    # Search encyclopedia topics rather than whole conversational sentences. The
    # original question and its requested date remain in the answer context.
    query = re.sub(
        r"\b(?:in|using)\s+(?:(?:a|an|one|two|three|four|five|\d+|short|brief|detailed)\s+)*(?:paragraphs?|sentences?|bullet points?|words?|table)\b.*$",
        "",
        query,
        flags=re.I,
    )
    query = re.sub(
        r"\b(?:with|and include|including)\s+(?:a\s+)?(?:source|reference|citation)s?\b.*$",
        "",
        query,
        flags=re.I,
    )
    query = re.sub(r"\btamilnadu\b", "Tamil Nadu", query, flags=re.I)
    query = re.sub(r"\bCM\b", "chief minister", query, flags=re.I)
    query = re.sub(r"\bPM\b", "prime minister", query, flags=re.I)
    if re.search(r"\b(?:chief minister|prime minister|president|governor|ceo)\b", query, re.I):
        query = re.sub(r"\b20\d{2}\b", " ", query)
    query = re.sub(
        r"\b(?:can|could|would|you|please|tell|me|about|what|who|is|are|was|were|the|a|an|of|on|in|at|for|and|name|current|currently|latest|today|now|explain|give|list|search|web|online|yaaru|enna|sollu|sollunga)\b",
        " ",
        query,
        flags=re.I,
    )
    query = re.sub(r"[^\w\s-]", " ", query)
    return " ".join(query.split())[:240]


def _search_wikipedia(query: str) -> list[dict] | None:
    if not settings.WIKIPEDIA_SEARCH_ENABLED:
        return None
    topic = _wikipedia_query(query)
    if not topic:
        return None
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": topic,
        "gsrlimit": min(3, max(1, settings.WEB_SEARCH_MAX_RESULTS)),
        "prop": "extracts|info|revisions",
        "inprop": "url",
        "explaintext": 1,
        "exintro": 1,
        "exlimit": "max",
        "rvprop": "timestamp",
        "format": "json",
        "formatversion": 2,
    }
    try:
        _, _, raw = _fetch(
            "https://en.wikipedia.org/w/api.php?" + urlencode(params),
            time.monotonic() + settings.WEB_FETCH_TIMEOUT_SECONDS,
        )
        data = json.loads(raw)
        pages = data.get("query", {}).get("pages", [])
        results = []
        for page in sorted(pages, key=lambda item: item.get("index", 999))[:3]:
            content = str(page.get("extract") or "")[:12000]
            url = str(page.get("fullurl") or "")
            if not content.strip() or urlsplit(url).hostname != "en.wikipedia.org":
                continue
            revisions = page.get("revisions") or []
            results.append(
                {
                    "title": str(page.get("title") or "Wikipedia")[:200],
                    "url": url,
                    "content": content,
                    "source_type": "Wikipedia introduction retrieved live",
                    "page_updated_at": revisions[0].get("timestamp") if revisions else None,
                }
            )
        return results or None
    except (WebPageError, ValueError, TypeError, AttributeError, KeyError) as error:
        logger.warning("chat.wikipedia_search_failed", extra={"reason": type(error).__name__})
        return None
