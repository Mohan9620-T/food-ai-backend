"""Deterministic explicit-content filter for web search results, no LLM call.

Phrases rather than single words where a word has an ordinary meaning too:
"nude lipstick", "naked eye", and similar everyday uses must never match -
only "nude"/"naked" immediately followed by an explicit noun do.
"""

import re
from urllib.parse import urlsplit

_EXPLICIT_NOUN = (
    r"(?:pics?|photos?|images?|videos?|clips?|scenes?|gifs?|pictures?|"
    r"women|men|girls?|boys?|body|bodies|selfies?)"
)

_EXPLICIT = re.compile(
    r"\bporn\w*\b|\bxxx\b|\bnsfw\b|\bhentai\b|\brule\s?34\b|\bonlyfans\b|"
    # Bare "nude" (singular) has ordinary meanings ("nude lipstick") and is only
    # explicit when paired with a noun; "nudes" (plural slang for explicit
    # photos) and "nudity" have no such everyday alternate meaning.
    r"\bnudes\b|\bnudity\b|\bnude\s+" + _EXPLICIT_NOUN + r"\b|"
    r"\bnaked\s+" + _EXPLICIT_NOUN + r"\b|"
    r"\bsex\s?(?:videos?|tapes?|cams?|chat)\b|\berotic\w*\b|\bmilf\b|"
    r"\bdick\s?pics?\b|\bboobs\b|\btopless\b|\bstriptease\b|\bbdsm\b|"
    r"\bxvideos\b|\bxhamster\b|\bchaturbate\b",
    re.IGNORECASE,
)

_EXPLICIT_DOMAINS = [
    "pornhub.com",
    "xvideos.com",
    "xhamster.com",
    "redtube.com",
    "youporn.com",
    "onlyfans.com",
    "chaturbate.com",
    "brazzers.com",
    "xnxx.com",
]

_EXPLICIT_HOST = re.compile(
    "|".join(re.escape(domain.split(".")[0]) for domain in _EXPLICIT_DOMAINS),
    re.IGNORECASE,
)


def asks_for_explicit(text: str) -> bool:
    """Checked against the search query before any provider call is made."""
    return bool(_EXPLICIT.search(text))


def is_explicit_source(result: dict) -> bool:
    """Checked against each search result after the provider call returns."""
    url = str(result.get("url") or "")
    host = urlsplit(url).hostname or ""
    text = " ".join(str(result.get(key) or "") for key in ("title", "content", "url"))
    return bool(_EXPLICIT_HOST.search(host) or _EXPLICIT.search(text))


def clean(results: list[dict]) -> list[dict]:
    return [result for result in results if not is_explicit_source(result)]


def explicit_domains() -> list[str]:
    """Passed as Tavily's exclude_domains - Tavily has no native safesearch flag."""
    return list(_EXPLICIT_DOMAINS)
