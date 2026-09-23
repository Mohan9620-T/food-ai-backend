"""Bounded, anonymous reads of public pages; never connect to private networks."""

import base64
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit

from app.config import settings

MAX_BYTES = 1_000_000
MAX_TEXT_CHARS = 18000


class WebPageError(ValueError):
    pass


def extract_urls(message: str) -> list[str]:
    return list(
        dict.fromkeys(
            url.rstrip(".,;:!?)]}")
            for url in re.findall(r'https?://[^\s<>"\[\]]+', message, flags=re.IGNORECASE)
        )
    )[:2]


def _public_target(url: str) -> tuple[str, int, str]:
    parsed = urlsplit(url)
    host = parsed.hostname
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username
        or parsed.password
        or len(url) > 2048
        or any(ord(c) < 32 for c in url)
    ):
        raise WebPageError("Only public HTTP or HTTPS pages can be read.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in {80, 443}:
        raise WebPageError("Only standard public web ports are supported.")
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise WebPageError("The website address could not be resolved.")
    ips = [str(address[4][0]) for address in addresses]
    if any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise WebPageError("Private, local and reserved network addresses cannot be read.")
    return host, port, ips[0]


def _fetch(url: str, deadline: float) -> tuple[str, str, str]:
    for _ in range(4):
        host, port, ip = _public_target(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WebPageError("The website took too long to respond.")
        parsed = urlsplit(url)
        connection = http.client.HTTPConnection(host, port, timeout=remaining)
        try:
            # Pin the validated address, preserving Host and TLS certificate/SNI checks.
            connection.sock = socket.create_connection((ip, port), timeout=remaining)
            if parsed.scheme == "https":
                connection.sock = ssl.create_default_context().wrap_socket(
                    connection.sock, server_hostname=host
                )
            path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
            if parsed.query:
                path += "?" + quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
            connection.request(
                "GET",
                path,
                headers={
                    "User-Agent": "FoodAI-LinkReader/1.0",
                    "Accept": "text/html, text/plain, application/json",
                    "Accept-Encoding": "identity",
                },
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise WebPageError("The website returned an invalid redirect.")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise WebPageError(
                    f"The website returned HTTP {response.status}; it may require sign-in or be unavailable."
                )
            media_type = response.getheader("Content-Type", "").split(";")[0].lower()
            if media_type not in {
                "text/html",
                "application/xhtml+xml",
                "text/plain",
                "text/markdown",
                "application/json",
            }:
                raise WebPageError(
                    "This link is not a readable text page. Upload the file to analyze it."
                )
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise WebPageError("The website returned an unsupported compressed response.")
            data = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WebPageError("The website took too long to respond.")
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(65536, MAX_BYTES + 1 - len(data)))
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise WebPageError(
                        "The page is too large to read. Please link to a specific section or file."
                    )
                if not chunk:
                    break
            return url, media_type, data.decode("utf-8", errors="replace")
        finally:
            connection.close()
    raise WebPageError("The website redirected too many times.")


class _PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.hidden: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self.hidden.append(tag)
        if tag == "title":
            self.in_title = True
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()
        if tag == "title":
            self.in_title = False
        if tag in {"td", "th"}:
            self.parts.append(" | ")
        elif tag in {"p", "div", "li", "tr", "section"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.hidden:
            return
        if self.in_title:
            self.title_parts.append(data)
        else:
            self.parts.append(data)


def _github(url: str, deadline: float) -> list[dict[str, str]] | None:
    parsed = urlsplit(url)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or not all(re.fullmatch(r"[\w.-]+", part) for part in parts):
        return None
    owner, repo = parts
    repo = repo.removesuffix(".git")
    api = f"https://api.github.com/repos/{owner}/{repo}"
    _, _, raw = _fetch(api, deadline)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise WebPageError("The repository returned an invalid response.")
    repository_url = f"https://github.com/{owner}/{repo}"
    facts = {
        key: data.get(key)
        for key in (
            "full_name",
            "description",
            "language",
            "default_branch",
            "updated_at",
            "topics",
            "archived",
        )
    }
    result = [
        {
            "title": f"{owner}/{repo}",
            "url": repository_url,
            "content": "Public repository metadata: " + json.dumps(facts),
        }
    ]
    try:
        _, _, raw = _fetch(api + "/readme", deadline)
        readme = json.loads(raw)
        content = base64.b64decode(readme["content"]).decode("utf-8", errors="replace")
        result.append(
            {
                "title": f"{repo} README",
                "url": str(readme.get("html_url") or repository_url),
                "content": content[:MAX_TEXT_CHARS],
            }
        )
    except (
        WebPageError,
        KeyError,
        ValueError,
        TypeError,
        AttributeError,
        OSError,
        http.client.HTTPException,
    ):
        result[0]["content"] += "\nREADME could not be retrieved."
    try:
        _, _, raw = _fetch(api + "/contents", deadline)
        entries = json.loads(raw)
        listing = ", ".join(str(entry["name"]) for entry in entries[:100])
        result[0]["content"] += "\nRoot entries (names only, not contents): " + listing
    except (WebPageError, KeyError, ValueError, TypeError, OSError, http.client.HTTPException):
        pass
    result[0]["content"] += (
        "\nScope: metadata, available README excerpt and root listing only. The full codebase has NOT been read."
    )
    return result


def read_page(url: str) -> list[dict[str, str]]:
    deadline = time.monotonic() + settings.WEB_FETCH_TIMEOUT_SECONDS
    try:
        github = _github(url, deadline)
        if github is not None:
            return github
        final_url, media_type, content = _fetch(url, deadline)
        title = urlsplit(final_url).hostname or "Web page"
        if media_type in {"text/html", "application/xhtml+xml"}:
            parser = _PageText()
            parser.feed(content)
            title = "".join(parser.title_parts).strip() or title
            content = "\n".join(
                line.strip() for line in "".join(parser.parts).splitlines() if line.strip()
            )
        if not content.strip():
            raise WebPageError(
                "No readable text was returned. This page may require JavaScript or sign-in."
            )
        truncated = len(content) > MAX_TEXT_CHARS
        return [
            {
                "title": title[:200],
                "url": final_url,
                "content": content[:MAX_TEXT_CHARS]
                + ("\n[Page excerpt; remaining text omitted.]" if truncated else ""),
            }
        ]
    except WebPageError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, http.client.HTTPException) as error:
        raise WebPageError(
            "The public page could not be read. Please retry or paste its content."
        ) from error
