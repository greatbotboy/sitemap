"""Crawl one site and collect HTML page URLs."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from urllib.robotparser import RobotFileParser

from sitemapgen.models import SitemapError, UrlEntry
from sitemapgen.page import extract_page
from sitemapgen.urls import normalize_url, same_origin, url_key

_MAX_BODY = 2_000_000
_MAX_REDIRECTS = 10
_REDIRECTS = {301, 302, 303, 307, 308}
_HTML_TYPES = {"text/html", "application/xhtml+xml"}


class _NoRedirect(HTTPRedirectHandler):
    """Surface redirects to the crawler instead of following them blindly."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, msg, headers, fp)


_OPENER = build_opener(_NoRedirect)


@dataclass(frozen=True)
class _Page:
    url: str
    content_type: str
    body: bytes
    last_modified: str | None
    x_robots: str | None


def crawl(
    start_url: str,
    *,
    max_pages: int | None = None,
    delay: float = 0.0,
    user_agent: str = "sitemapgen",
    respect_robots: bool = True,
    timeout: float = 10.0,
) -> list[UrlEntry]:
    """Fetch HTML pages on the start URL's scheme, host, and port.

    Fragments are removed. Redirects that stay on the same origin are followed
    and the final URL is kept. ``robots.txt`` is honored unless
    ``respect_robots`` is false. Only ``text/html`` and ``application/xhtml+xml``
    responses become entries. Each entry includes a ``lastmod`` when the page
    or its ``Last-Modified`` header has one, plus image and hreflang data when
    present. Canonical URLs replace duplicates. ``noindex`` pages are omitted.
    """

    _check_crawl_args(max_pages, delay, user_agent, timeout)
    try:
        origin = normalize_url(start_url)
    except ValueError as exc:
        raise SitemapError(str(exc)) from exc

    robots = _load_robots(origin, timeout) if respect_robots else None
    seen: set[tuple[str, str, int, str, str]] = set()
    fetched: set[tuple[str, str, int, str, str]] = set()
    queue: deque[str] = deque()
    order: list[tuple[str, str, int, str, str]] = []
    stored: dict[tuple[str, str, int, str, str], tuple[UrlEntry, bool]] = {}

    def enqueue(candidate: str) -> None:
        try:
            normalized = normalize_url(candidate)
        except ValueError:
            return
        if not same_origin(normalized, origin):
            return
        key = url_key(normalized)
        if key in seen:
            return
        seen.add(key)
        queue.append(normalized)

    enqueue(origin)
    requests_made = 0
    html_pages = 0
    while queue:
        if max_pages is not None and html_pages >= max_pages:
            break
        current = queue.popleft()
        current_key = url_key(current)
        if current_key in fetched:
            continue
        if robots is not None and not robots.can_fetch(user_agent, current):
            continue
        if delay and requests_made:
            time.sleep(delay)
        requests_made += 1
        page = _fetch(current, timeout=timeout, user_agent=user_agent, origin=origin)
        if page is None:
            continue
        try:
            final = normalize_url(page.url)
        except ValueError:
            continue
        if not same_origin(final, origin):
            continue
        final_key = url_key(final)
        seen.add(final_key)
        if final_key in fetched:
            continue
        fetched.add(final_key)
        if robots is not None and not robots.can_fetch(user_agent, final):
            continue
        if _media_type(page.content_type) not in _HTML_TYPES:
            continue
        html_pages += 1
        facts = extract_page(
            _decode(page.body, page.content_type),
            final,
            last_modified=page.last_modified,
            x_robots=page.x_robots,
        )
        for link in facts.links:
            enqueue(link)
        if not facts.include:
            stale = stored.get(final_key)
            if stale is not None and not stale[1]:
                del stored[final_key]
                order.remove(final_key)
            continue
        try:
            loc = normalize_url(facts.loc)
        except ValueError:
            continue
        if not same_origin(loc, origin):
            continue
        if robots is not None and not robots.can_fetch(user_agent, loc):
            continue
        entry = UrlEntry(
            loc,
            lastmod=facts.lastmod,
            images=facts.images,
            alternates=facts.alternates,
        )
        _store(stored, order, entry, exact=url_key(loc) == final_key)
    return [stored[key][0] for key in order]


def _check_crawl_args(
    max_pages: int | None,
    delay: float,
    user_agent: str,
    timeout: float,
) -> None:
    if max_pages is not None and (
        isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1
    ):
        raise SitemapError("max_pages must be a positive integer")
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or delay < 0:
        raise SitemapError("delay must be zero or greater")
    if not isinstance(user_agent, str) or not user_agent.strip():
        raise SitemapError("user_agent must be a non-empty string")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise SitemapError("timeout must be greater than zero")


def _load_robots(origin: str, timeout: float) -> RobotFileParser | None:
    parts = urlsplit(origin)
    robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        with urlopen(robots_url, timeout=timeout) as response:
            body = response.read()
    except HTTPError as exc:
        try:
            # Match RobotFileParser.read: 401/403 disallow the site, other
            # 4xx responses mean no rules were published, and 5xx leaves
            # can_fetch closed.
            if exc.code in (401, 403) or exc.code >= 500:
                parser.disallow_all = True
            else:
                parser.allow_all = True
        finally:
            exc.close()
        return parser
    except (URLError, TimeoutError, OSError, ValueError):
        return None
    parser.parse(body.decode("utf-8", "surrogateescape").splitlines())
    return parser


def _fetch(url: str, *, timeout: float, user_agent: str, origin: str) -> _Page | None:
    current = url
    for _ in range(_MAX_REDIRECTS):
        request = Request(current, headers={"User-Agent": user_agent})
        try:
            with _OPENER.open(request, timeout=timeout) as response:
                final = response.geturl()
                if not same_origin(final, origin):
                    return None
                body = response.read(_MAX_BODY + 1)
                if len(body) > _MAX_BODY:
                    return None
                return _Page(
                    final,
                    response.headers.get("Content-Type", ""),
                    body,
                    response.headers.get("Last-Modified"),
                    response.headers.get("X-Robots-Tag"),
                )
        except HTTPError as exc:
            try:
                if exc.code not in _REDIRECTS:
                    return None
                location = exc.headers.get("Location")
                if not location:
                    return None
                nxt = urljoin(current, location.strip())
                parts = urlsplit(nxt)
                nxt = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
                if parts.scheme not in ("http", "https") or not same_origin(nxt, origin):
                    return None
                current = nxt
            finally:
                exc.close()
        except (URLError, TimeoutError, OSError, ValueError):
            return None
    return None


def _store(
    stored: dict[tuple[str, str, int, str, str], tuple[UrlEntry, bool]],
    order: list[tuple[str, str, int, str, str]],
    entry: UrlEntry,
    *,
    exact: bool,
) -> None:
    key = url_key(entry.loc)
    current = stored.get(key)
    if current is None:
        stored[key] = (entry, exact)
        order.append(key)
        return
    _old, old_exact = current
    if exact and not old_exact:
        stored[key] = (entry, True)


def _media_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _decode(body: bytes, content_type: str) -> str:
    charset = "utf-8"
    for piece in content_type.split(";")[1:]:
        piece = piece.strip()
        if piece.lower().startswith("charset="):
            charset = piece.split("=", 1)[1].strip().strip("\"'") or "utf-8"
            break
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")
