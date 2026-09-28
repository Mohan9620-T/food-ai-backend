"""Read a requested channel's own public upload list, never generic video hits."""

import http.client
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlsplit

from app.config import settings
from app.schemas.chat import ChatHistoryMessage
from app.services import web_search_provider
from app.services.web_page_reader import WebPageError, _fetch, extract_urls

_CHANNEL_ID = re.compile(r"UC[\w-]{22}", re.ASCII)
_VIDEO_ID = re.compile(r"[\w-]{11}", re.ASCII)
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
_COUNTS = {
    word: number
    for number, word in enumerate("one two three four five six seven eight nine ten".split(), 1)
}


def channel_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in _HOSTS
            or parsed.username
            or parsed.password
            or parsed.port
        ):
            return None
        parts = unquote(parsed.path).strip("/").split("/")
        if re.fullmatch(r"@[\w.-]{2,100}", parts[0]):
            path = parts[0]
        elif len(parts) >= 2 and parts[0] in {"channel", "c", "user"}:
            if parts[0] == "channel" and not _CHANNEL_ID.fullmatch(parts[1]):
                return None
            if not re.fullmatch(r"[\w.-]{1,100}", parts[1]):
                return None
            path = "/".join(parts[:2])
        else:
            return None
        return "https://www.youtube.com/" + quote(path, safe="/@-._")
    except ValueError:
        return None


def _normal(value: str) -> str:
    return re.sub(r"[\W_]+", "", value).casefold()


def _target(text: str) -> str | None:
    for url in extract_urls(text):
        if result := channel_url(url):
            return result
    if handle := re.search(r"(?<![\w/])@[\w.-]{2,100}", text):
        return "https://www.youtube.com/" + quote(handle.group().rstrip("."), safe="@-._")
    patterns = (
        r"\b(?:from|on|of|by)\s+(?:the\s+)?(.+?)(?:\s+(?:youtube\s+)?channel\b|[?!\n]|$)",
        r"^(.+?)\s+(?:youtube\s+)?channel\b",
        r"\b(?:youtube\s+)?channel\s+(?:(?:named|called)\s+)?(.+?)(?:[?!\n]|$)",
        r"\b(?:latest|recent|newest)\s+(.+?)\s+videos?\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        name = match.group(1).strip(" .,:!?\"'")
        name = re.sub(r"^(?:can|could|would)\s+you\s+", "", name, flags=re.I)
        name = re.sub(
            r"^(?:do you know(?: about)?|tell me about|what about|how about|please|show me|show|list|find|display)\s+",
            "",
            name,
            flags=re.I,
        )
        name = re.sub(
            r"^(?:the\s+)?(?:latest|recent|newest)?\s*(?:(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+)?(?:videos?|uploads?)\s*(?:from|on|of|by)?\s*",
            "",
            name,
            flags=re.I,
        )
        name = re.sub(r"\s+(?:on|from|of|by|the|youtube)\s*$", "", name, flags=re.I).strip()
        if (
            name
            and len(name) <= 100
            and not name.isdecimal()
            and name.casefold() not in _COUNTS
            and not re.fullmatch(
                r"(?:(?:the|this|that|same|a|another|different|other|channel|youtube|latest|recent|video|videos|please|from|on|of|by|show|list|me)\s*)+",
                name,
                re.I,
            )
        ):
            return name
    return None


@dataclass(frozen=True)
class VideoRequest:
    target: str | None
    count: int = 5
    tab: str = "videos"


def video_request(message: str, history: list[ChatHistoryMessage]) -> VideoRequest | None:
    if re.search(
        r"\bvideo\s+games?\b|\b(?:create|generate|edit|make)\s+(?:a\s+)?video\b|\b(?:ideas|scripts|thumbnails)\b",
        message,
        re.I,
    ):
        return None
    previous = history[-10:]
    channel_context = bool(re.search(r"\byoutube\b|\bchannel\b|@[\w.-]+", message, re.I)) or any(
        re.search(r"\byoutube\b|\bchannel\b|youtube\.com/", turn.content[:2000], re.I)
        for turn in previous
    )
    has_video = re.search(r"\b(?:videos?|uploads?|shorts?)\b", message, re.I)
    channel_latest = re.search(
        r"\b(?:latest|newest)\b.{0,40}\bchannel\b|\bchannel\b.{0,40}\b(?:latest|newest)\b",
        message,
        re.I,
    )
    if not channel_context or not (has_video or channel_latest):
        return None
    if not re.search(
        r"\b(?:show|list|latest|recent|newest|last|find|give|display|watch|play)\b", message, re.I
    ):
        return None
    target = _target(message)
    if not target and not re.search(
        r"\b(?:another|different|other|vera)\s+channel\b", message, re.I
    ):
        # Only the same conversation's recent user turns provide the referent.
        # An assistant URL can help resolve a name, but is still checked live.
        for turn in reversed(previous):
            if (
                turn.role == "user"
                and re.search(r"\byoutube\b|\bchannel\b|(?<!\w)@[\w.-]+", turn.content[:2000], re.I)
                and (target := _target(turn.content[:2000]))
            ):
                break
        if not target:
            for turn in reversed(previous):
                if turn.role == "assistant":
                    candidates = [
                        channel_url(url)
                        for url in extract_urls(turn.content.split("### Sources")[0][:2000])
                    ]
                    if len(set(filter(None, candidates))) == 1:
                        target = next(value for value in candidates if value)
                        break
    number = re.search(
        r"\b(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:latest\s+|recent\s+)?(?:videos?|uploads?|shorts?)\b",
        message,
        re.I,
    )
    count = (
        min(
            30,
            max(
                1,
                int(number.group(1))
                if number.group(1).isdigit()
                else _COUNTS[number.group(1).casefold()],
            ),
        )
        if number
        else (1 if re.search(r"\b(?:latest|newest|last)\s+video\b", message, re.I) else 5)
    )
    return VideoRequest(
        target, count, "shorts" if re.search(r"\bshorts?\b", message, re.I) else "videos"
    )


@dataclass(frozen=True)
class Video:
    id: str
    title: str
    published: str = "Date not provided"


def _text(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    return str(
        value.get("simpleText")
        or value.get("content")
        or "".join(
            str(run.get("text", "")) for run in value.get("runs", []) if isinstance(run, dict)
        )
    )


def _nodes(value, key):
    if isinstance(value, dict):
        if key in value:
            yield value[key]
        for item in value.values():
            yield from _nodes(item, key)
    elif isinstance(value, list):
        for item in value:
            yield from _nodes(item, key)


def _initial_data(raw: str) -> dict:
    match = re.search(
        r'(?:var\s+ytInitialData|(?:window\["ytInitialData"\])|ytInitialData)\s*=\s*', raw
    )
    if not match:
        raise WebPageError("YouTube did not return a readable public channel page.")
    data, _ = json.JSONDecoder().raw_decode(raw[match.end() :])
    if not isinstance(data, dict):
        raise WebPageError("YouTube returned an invalid channel page.")
    return data


def _identity(data: dict, requested: str) -> tuple[str, str]:
    metadata = data.get("metadata", {}).get("channelMetadataRenderer", {})
    identity = str(metadata.get("externalId", ""))
    title = str(metadata.get("title", ""))
    if not _CHANNEL_ID.fullmatch(identity) or not title:
        raise WebPageError("The channel identity could not be verified.")
    aliases = [str(value) for value in metadata.get("ownerUrls", [])]
    aliases.extend([str(metadata.get("vanityChannelUrl", "")), str(metadata.get("channelUrl", ""))])
    locators = {channel_url(value) for value in aliases}
    locators.add(f"https://www.youtube.com/channel/{identity}")
    if requested.startswith("https://"):
        if "/channel/" in requested:
            matches = requested == f"https://www.youtube.com/channel/{identity}"
        else:
            matches = requested.casefold() in {value.casefold() for value in locators if value}
    else:
        names = {_normal(title)} | {
            _normal(unquote(urlsplit(value).path).split("/")[-1])
            for value in aliases
            if channel_url(value)
        }
        matches = _normal(requested) in names
    if not matches:
        raise WebPageError("The returned channel does not match the requested channel.")
    return identity, title


def _page_videos(data: dict, tab_name: str) -> tuple[list[Video], bool]:
    tabs = data.get("contents", {}).get("twoColumnBrowseResultsRenderer", {}).get("tabs", [])
    selected: dict = next(
        (
            item.get("tabRenderer", {})
            for item in tabs
            if item.get("tabRenderer", {}).get("selected")
        ),
        {},
    )
    if str(selected.get("title", "")).casefold() != tab_name:
        return [], False
    grid = selected.get("content", {}).get("richGridRenderer", {})
    # Only direct upload cards in the requested tab. Shelves, Home-page
    # recommendations, playlists, adverts and unrelated nested cards are excluded.
    latest = any(
        chip.get("selected") and chip.get("text") == "Latest"
        for chip in _nodes(grid.get("header", {}), "chipViewModel")
    ) or any(
        chip.get("isSelected") and _text(chip.get("text")) == "Latest"
        for chip in _nodes(grid.get("header", {}), "chipCloudChipRenderer")
    )
    videos = []
    seen = set()
    for item in grid.get("contents", []):
        card = item.get("richItemRenderer", {}).get("content", {})
        video = card.get("videoRenderer", {})
        model = card.get("lockupViewModel", {})
        if video:
            if video.get("upcomingEventData"):
                continue
            identity = str(video.get("videoId", ""))
            title = _text(video.get("title"))
            published = _text(video.get("publishedTimeText"))
            owner_data = video.get("ownerText", video.get("longBylineText", {}))
        elif model.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO":
            identity = str(model.get("contentId", ""))
            metadata = model.get("metadata", {}).get("lockupMetadataViewModel", {})
            title = _text(metadata.get("title"))
            parts = [
                part
                for row in metadata.get("metadata", {})
                .get("contentMetadataViewModel", {})
                .get("metadataRows", [])
                for part in row.get("metadataParts", [])
            ]
            owner_data = parts
            published = next(
                (
                    _text(part.get("text"))
                    for part in parts
                    if re.search(r"\bago\b|\bStreamed\b|\bPremiered\b", _text(part.get("text")))
                ),
                "",
            )
        else:
            continue
        expected_owner = (
            data.get("metadata", {}).get("channelMetadataRenderer", {}).get("externalId")
        )
        owners = {
            str(node.get("browseId", ""))
            for node in _nodes(owner_data, "browseEndpoint")
            if _CHANNEL_ID.fullmatch(str(node.get("browseId", "")))
        }
        if owners and owners != {expected_owner}:
            continue
        if _VIDEO_ID.fullmatch(identity) and title and identity not in seen:
            videos.append(Video(identity, title, published or "Date not provided"))
            seen.add(identity)
    return videos, latest


def _feed_videos(identity: str, deadline: float) -> list[Video]:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={identity}"
    final, _, raw = _fetch(url, deadline, allow_xml=True)
    if urlsplit(final).hostname not in _HOSTS or re.search(r"<!DOCTYPE|<!ENTITY", raw, re.I):
        raise WebPageError("The channel feed could not be verified.")
    root = ET.fromstring(raw)
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    if root.findtext("yt:channelId", namespaces=ns) != identity:
        raise WebPageError("The feed belongs to a different channel.")
    entries = []
    seen = set()
    for entry in root.findall("a:entry", ns):
        video_id = entry.findtext("yt:videoId", default="", namespaces=ns)
        title = entry.findtext("a:title", default="", namespaces=ns)
        date = entry.findtext("a:published", default="", namespaces=ns)
        if (
            entry.findtext("yt:channelId", namespaces=ns) != identity
            or not _VIDEO_ID.fullmatch(video_id)
            or not title
            or video_id in seen
        ):
            continue
        timestamp = datetime.fromisoformat(date.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            continue
        entries.append(
            (
                timestamp,
                Video(
                    video_id,
                    title,
                    timestamp.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC"),
                ),
            )
        )
        seen.add(video_id)
    return [video for _, video in sorted(entries, key=lambda item: item[0], reverse=True)]


def _label(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()<>#!|])", r"\\\1", " ".join(value.split()))


def channel_video_answer(request: VideoRequest) -> str:
    if not request.target:
        return "Which YouTube channel should I check? Send its channel link or @handle so I can list the correct videos."
    target = request.target
    deadline = (
        time.monotonic() + settings.WEB_FETCH_TIMEOUT_SECONDS + settings.TAVILY_TIMEOUT_SECONDS
    )
    if target.startswith("https://"):
        candidates = [target]
    else:
        results = web_search_provider.search(f'"{target}" YouTube channel') or []
        candidates = list(
            dict.fromkeys(url for item in results if (url := channel_url(str(item.get("url", "")))))
        )[:3]
    matched: dict[str, tuple[str, str, list[Video], bool]] = {}
    for candidate in candidates:
        try:
            final, _, raw = _fetch(
                candidate + f"/{request.tab}?hl=en", deadline, max_bytes=2_000_000
            )
            if urlsplit(final).hostname not in _HOSTS:
                continue
            data = _initial_data(raw)
            identity, title = _identity(data, target)
            videos, latest = _page_videos(data, request.tab)
            matched[identity] = (title, final, videos, latest)
        except (
            WebPageError,
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
            RecursionError,
            http.client.HTTPException,
        ):
            continue
    if len(matched) != 1:
        return (
            "I couldn't uniquely verify that YouTube channel. Send its exact channel link or @handle; "
            "I won't substitute videos from a different channel."
        )
    identity, (title, page_url, videos, latest) = next(iter(matched.items()))
    scope = f"YouTube's {request.tab.title()} tab"
    if (not videos or not latest) and request.tab == "videos":
        try:
            videos = _feed_videos(identity, deadline)
            latest = True
            scope = "the channel's public upload feed (which may include Shorts)"
            page_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={identity}"
        except (
            WebPageError,
            OSError,
            ValueError,
            TypeError,
            ET.ParseError,
            http.client.HTTPException,
        ):
            videos = []
    channel = f"https://www.youtube.com/channel/{identity}/{request.tab}"
    if not videos or not latest:
        return f"I found [{_label(title)}]({channel}), but couldn't verify its latest uploads from YouTube right now. Please retry; I won't fill the list with unrelated search results."
    displayed = videos[: request.count]
    lines = [
        f"### Latest {'video' if len(displayed) == 1 else 'videos'} from [{_label(title)}]({channel})",
        "",
    ]
    for index, video in enumerate(displayed, 1):
        lines.append(
            f"{index}. **[{_label(video.title)}](https://www.youtube.com/watch?v={video.id})**  \n   Published: {_label(video.published)}."
        )
    lines.extend(
        [
            "",
            f"Showing {len(displayed)} recent {'upload' if len(displayed) == 1 else 'uploads'} from {scope}, newest first.",
        ]
    )
    if len(displayed) < request.count:
        lines.append(
            f"Only {len(displayed)} verified uploads were available in this response; {request.count} were requested."
        )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.extend(
        ["", "### Sources", f"- [{_label(title)} - {scope}]({page_url})", "", f"*Retrieved {now}.*"]
    )
    return "\n".join(lines)
