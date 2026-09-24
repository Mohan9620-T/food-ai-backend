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


# Recognize a small set of prompt words accidentally pasted against a filename.
# Match the whole suffix so .json, .html and .tsx are never shortened to .js/.h/.ts.
_KNOWN_URL_EXTENSIONS = tuple(
    sorted(
        {
            "example",
            "config",
            "gitignore",
            "dockerfile",
            "yaml",
            "json",
            "toml",
            "lock",
            "html",
            "yml",
            "env",
            "cfg",
            "ini",
            "txt",
            "md",
            "py",
            "js",
            "ts",
            "tsx",
            "jsx",
            "go",
            "rs",
            "rb",
            "java",
            "c",
            "cpp",
            "h",
            "sh",
            "csv",
            "sql",
            "log",
            "xml",
            "css",
        },
        key=len,
        reverse=True,
    )
)
_URL_EXTENSION_GLUE_PATTERN = re.compile(
    r"(?P<extension>\.(?:" + "|".join(_KNOWN_URL_EXTENSIONS) + r"))(?:go|read|check|please)$",
    re.IGNORECASE,
)


def _trim_glued_trailing_word(url: str) -> str:
    """Recover common missing-space typos in a path, preserving query and fragment."""
    parsed = urlsplit(url)
    match = _URL_EXTENSION_GLUE_PATTERN.search(parsed.path)
    if match is None:
        return url
    return parsed._replace(path=parsed.path[: match.end("extension")]).geturl()


def extract_urls(message: str) -> list[str]:
    return list(
        dict.fromkeys(
            _trim_glued_trailing_word(url.rstrip(".,;:!?)]}"))
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


def _fetch(
    url: str, deadline: float, *, extra_headers: dict[str, str] | None = None
) -> tuple[str, str, str]:
    # Keep credentials on the same origin, including scheme and port. In
    # particular, an HTTPS-to-HTTP redirect must never receive a GitHub token.
    original = urlsplit(url)
    original_origin = (
        original.scheme,
        original.hostname,
        original.port or (443 if original.scheme == "https" else 80),
    )
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
            headers = {
                "User-Agent": "FoodAI-LinkReader/1.0",
                "Accept": "text/html, text/plain, application/json",
                "Accept-Encoding": "identity",
            }
            if extra_headers and (parsed.scheme, host, port) == original_origin:
                headers.update(extra_headers)
            connection.request("GET", path, headers=headers)
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


# Files fetched from a "tree" (directory) URL are limited to these extensions, so a
# directory link cannot pull in binaries, lockfiles or other noise.
_GITHUB_TREE_RELEVANT_EXTENSIONS = (
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".rb",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".md",
    ".txt",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".cfg",
    ".ini",
    ".env",
    ".example",
    ".sql",
)

# Redacts values on lines that look like KEY=VALUE / KEY: VALUE config entries whose
# key name suggests a secret, wherever a GitHub file is read - not just files that
# look like config by name, since a real secret could appear in any file.
_SECRET_LINE_PATTERN = re.compile(
    r"^(?P<indent>[ \t]*)(?P<key>(?=[A-Za-z0-9_]*"
    r"(?:SECRET|TOKEN|PASSWORD|PASSWD|PASS|API_KEY|APIKEY|PRIVATE_KEY|CREDENTIAL|AUTH))"
    r"[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P<sep>\s*[:=]\s*)(?P<value>.+)$",
    re.IGNORECASE,
)
_PLACEHOLDER_VALUE_PATTERN = re.compile(
    r"^[\"']?(your[-_ ]|replace|change[-_]?me|example|placeholder|todo|xxx|\.\.\.|<|\$\{)",
    re.IGNORECASE,
)


def _redact_secrets(content: str) -> str:
    """Replace apparent secret values with a placeholder before this ever reaches the LLM."""
    lines = []
    for line in content.splitlines():
        match = _SECRET_LINE_PATTERN.match(line)
        if match:
            value = match.group("value").strip()
            if value and not _PLACEHOLDER_VALUE_PATTERN.match(value):
                line = f"{match.group('indent')}{match.group('key')}{match.group('sep')}[REDACTED]"
        lines.append(line)
    return "\n".join(lines)


def _github_auth_headers() -> dict[str, str]:
    return {"Authorization": f"token {settings.GITHUB_TOKEN}"} if settings.GITHUB_TOKEN else {}


def _github_error(error: Exception, owner: str, repo: str) -> WebPageError:
    if "HTTP 404" in str(error):
        if settings.GITHUB_TOKEN:
            return WebPageError(
                f"GitHub path not found in {owner}/{repo} - check the branch and path are correct."
            )
        return WebPageError(
            f"{owner}/{repo} could not be accessed - it may be private, or the branch/path may not "
            "exist. Reading private repositories requires GITHUB_TOKEN to be configured on the server; "
            "that is not currently configured."
        )
    return WebPageError(f"GitHub could not be read: {error}")


def _github_repo_overview(owner: str, repo: str, deadline: float) -> list[dict[str, str]]:
    api = f"{settings.GITHUB_API_BASE_URL}/repos/{owner}/{repo}"
    headers = _github_auth_headers()
    try:
        _, _, raw = _fetch(api, deadline, extra_headers=headers)
    except WebPageError as error:
        raise _github_error(error, owner, repo) from error
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
        _, _, raw = _fetch(api + "/readme", deadline, extra_headers=headers)
        readme = json.loads(raw)
        content = base64.b64decode(readme["content"]).decode("utf-8", errors="replace")
        result.append(
            {
                "title": f"{repo} README",
                "url": str(readme.get("html_url") or repository_url),
                "content": _redact_secrets(content[:MAX_TEXT_CHARS]),
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
        _, _, raw = _fetch(api + "/contents", deadline, extra_headers=headers)
        entries = json.loads(raw)
        listing = ", ".join(str(entry["name"]) for entry in entries[:100])
        result[0]["content"] += "\nRoot entries (names only, not contents): " + listing
    except (WebPageError, KeyError, ValueError, TypeError, OSError, http.client.HTTPException):
        pass
    result[0]["content"] += (
        "\nScope: metadata, available README excerpt and root listing only. The full codebase has NOT been read."
    )
    return result


def _github_contents_url(owner: str, repo: str, path: str, branch: str) -> str:
    return f"{settings.GITHUB_API_BASE_URL}/repos/{owner}/{repo}/contents/{quote(path)}?ref={quote(branch)}"


def _github_resolve_ref(
    owner: str, repo: str, ref_parts: list[str], deadline: float
) -> tuple[str, str, str]:
    """Resolve which prefix of ref_parts is the branch, trying shortest-branch first.

    A branch name can itself contain "/" (e.g. "feat/xyz"), and GitHub's own
    tree/blob URLs give no syntactic way to tell where the branch ends and the
    path begins - "owner/repo/tree/feat/xyz/src" is genuinely ambiguous from the
    URL alone. This tries each split against the Contents API and returns the
    first one GitHub accepts, exactly like real "paste a GitHub URL" tools do.
    Bounded by the URL's own path depth (one extra request per ambiguous level).
    """
    last_error: WebPageError | None = None
    for split in range(1, len(ref_parts) + 1):
        branch = "/".join(ref_parts[:split])
        path = "/".join(ref_parts[split:])
        try:
            _, _, raw = _fetch(
                _github_contents_url(owner, repo, path, branch),
                deadline,
                extra_headers=_github_auth_headers(),
            )
            return branch, path, raw
        except WebPageError as error:
            last_error = error
            if "HTTP 404" not in str(error):
                raise _github_error(error, owner, repo) from error
    raise (
        _github_error(last_error, owner, repo)
        if last_error
        else WebPageError("The branch or path could not be resolved.")
    )


def _github_fetch_file(
    owner: str, repo: str, branch: str, path: str, deadline: float, *, raw: str | None = None
) -> list[dict[str, str]]:
    """Parse (or fetch and parse) one Contents API file response into a result."""
    if raw is None:
        try:
            _, _, raw = _fetch(
                _github_contents_url(owner, repo, path, branch),
                deadline,
                extra_headers=_github_auth_headers(),
            )
        except WebPageError as error:
            raise _github_error(error, owner, repo) from error
    data = json.loads(raw)
    if isinstance(data, list):
        raise WebPageError(f"{path} is a directory, not a file - use a tree URL to read it.")
    if not isinstance(data, dict) or data.get("type") != "file":
        raise WebPageError(f"{path} is not a readable file.")
    if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
        raise WebPageError(
            f"{path} is too large or in a format the GitHub API cannot return inline "
            "(binary/large-file blobs must be uploaded to be read)."
        )
    try:
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    except (ValueError, TypeError) as error:
        raise WebPageError(f"{path} could not be decoded as text.") from error
    content = _redact_secrets(content)
    truncated = len(content) > MAX_TEXT_CHARS
    return [
        {
            "title": f"{owner}/{repo}: {path}",
            "url": str(
                data.get("html_url") or f"https://github.com/{owner}/{repo}/blob/{branch}/{path}"
            ),
            "content": content[:MAX_TEXT_CHARS]
            + ("\n[File excerpt; remaining text omitted.]" if truncated else ""),
        }
    ]


def _github_blob(
    owner: str, repo: str, ref_parts: list[str], deadline: float
) -> list[dict[str, str]]:
    """Read one file's actual content from a github.com/.../blob/... URL."""
    branch, path, raw = _github_resolve_ref(owner, repo, ref_parts, deadline)
    return _github_fetch_file(owner, repo, branch, path, deadline, raw=raw)


def _github_tree(
    owner: str, repo: str, ref_parts: list[str], deadline: float
) -> list[dict[str, str]]:
    """List a directory and read a bounded set of its relevant files."""
    branch, path, raw = _github_resolve_ref(owner, repo, ref_parts, deadline)
    entries = json.loads(raw)
    if not isinstance(entries, list):
        raise WebPageError(f"{path or '/'} is a file, not a directory - use a blob URL to read it.")
    entry_names = sorted(
        str(entry.get("name")) for entry in entries if isinstance(entry, dict) and entry.get("name")
    )
    candidates = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("type") == "file"
        and str(entry.get("name", "")).lower().endswith(_GITHUB_TREE_RELEVANT_EXTENSIONS)
    ]
    selected = candidates[: settings.GITHUB_TREE_MAX_FILES]
    tree_url = f"https://github.com/{owner}/{repo}/tree/{branch}/{path}".rstrip("/")
    result = [
        {
            "title": f"{owner}/{repo}: {path or '/'} (directory listing)",
            "url": tree_url,
            "content": (
                f"Directory entries in {path or '/'}: " + ", ".join(entry_names)
                if entry_names
                else f"{path or '/'} is empty."
            )
            + f"\nScope: listing plus the content of {len(selected)} of "
            f"{len(candidates)} relevant file(s) below (capped at "
            f"{settings.GITHUB_TREE_MAX_FILES}). Other files/subdirectories were NOT read.",
        }
    ]
    for entry in selected:
        entry_path = str(entry.get("path") or "/".join(filter(None, [path, str(entry["name"])])))
        try:
            result.extend(_github_fetch_file(owner, repo, branch, entry_path, deadline))
        except WebPageError as error:
            result.append(
                {
                    "title": f"{owner}/{repo}: {entry_path}",
                    "url": f"https://github.com/{owner}/{repo}/blob/{branch}/{entry_path}",
                    "content": f"This file could not be read: {error}",
                }
            )
    return result


def _github(url: str, deadline: float) -> list[dict[str, str]] | None:
    parsed = urlsplit(url)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2 or not all(re.fullmatch(r"[\w.-]+", part) for part in parts[:2]):
        return None
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if len(parts) == 2:
        return _github_repo_overview(owner, repo, deadline)
    kind = parts[2]
    if kind == "blob" and len(parts) >= 4:
        return _github_blob(owner, repo, parts[3:], deadline)
    if kind == "tree" and len(parts) >= 4:
        return _github_tree(owner, repo, parts[3:], deadline)
    # Not a recognized github.com content URL shape (e.g. /issues, /pull/123) -
    # fall through to the generic page fetch below.
    return None


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
