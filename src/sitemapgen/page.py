"""Read SEO fields from an HTML page."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from sitemapgen.models import AlternateLink, ImageInfo
from sitemapgen.urls import format_lastmod, normalize_url, same_origin

_MAX_IMAGES = 1_000
_MAX_ALTERNATES = 100


@dataclass(frozen=True)
class PageFacts:
    """Signals collected from one HTML response."""

    loc: str
    lastmod: str | None
    images: tuple[ImageInfo, ...]
    alternates: tuple[AlternateLink, ...]
    links: tuple[str, ...]
    include: bool


def extract_page(
    html: str,
    page_url: str,
    *,
    last_modified: str | None = None,
    x_robots: str | None = None,
) -> PageFacts:
    """Collect the sitemap fields present on a page.

    ``lastmod`` prefers an on-page modified time, then the ``Last-Modified``
    header, then a published time. A canonical URL on the same host replaces
    ``page_url``. Pages marked ``noindex`` are not included. ``nofollow`` drops
    the links that would otherwise be crawled.
    """

    parser = _PageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        parser = _PageParser()
    base = urljoin(page_url, parser.base_href) if parser.base_href else page_url
    directives = _robots_directives(x_robots, parser.robots)
    follow = "nofollow" not in directives
    links = _absolute_links(parser.hrefs, base) if follow else ()
    canonical = _absolute_http(parser.canonical, base) if parser.canonical else None
    include = "noindex" not in directives
    loc = page_url
    if canonical is not None:
        if same_origin(canonical, page_url):
            loc = canonical
            # nofollow drops ordinary links. The canonical URL is still fetched
            # so the sitemap can record that page's own dates and images.
            links = tuple(dict.fromkeys((*links, canonical))) if follow else (canonical,)
        else:
            include = False
    modified = _accept_date(parser.modified) or _jsonld_date(parser.jsonld, "dateModified")
    published = _accept_date(parser.published) or _jsonld_date(parser.jsonld, "datePublished")
    lastmod = modified or _http_date(last_modified) or published
    return PageFacts(
        loc=loc,
        lastmod=lastmod,
        images=_images(parser.images, base),
        alternates=_alternates(parser.alternates, base),
        links=links,
        include=include,
    )


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base_href: str | None = None
        self.hrefs: list[str] = []
        self.canonical: str | None = None
        self.modified: str | None = None
        self.published: str | None = None
        self.robots: list[str] = []
        self.alternates: list[tuple[str, str]] = []
        self.images: list[tuple[str, str]] = []
        self.jsonld: list[str] = []
        self._in_jsonld = False
        self._jsonld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs if key}
        name = tag.lower()
        if name == "base" and values.get("href") and self.base_href is None:
            self.base_href = values["href"]
        elif name == "a" and values.get("href"):
            self.hrefs.append(values["href"] or "")
        elif name == "link":
            self._link(values)
        elif name == "meta":
            self._meta(values)
        elif name == "img":
            src = values.get("src") or _srcset_first(values.get("srcset"))
            if src:
                self.images.append((src, values.get("alt") or ""))
        elif name == "script" and _media(values.get("type")) == "application/ld+json":
            self._in_jsonld = True
            self._jsonld_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._jsonld_parts))
            self._in_jsonld = False

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._jsonld_parts.append(data)

    def _link(self, values: dict[str, str | None]) -> None:
        rel = set((values.get("rel") or "").lower().split())
        href = values.get("href")
        if not href or "stylesheet" in rel:
            return
        if "canonical" in rel and self.canonical is None:
            self.canonical = href
        hreflang = values.get("hreflang")
        if "alternate" in rel and hreflang:
            self.alternates.append((hreflang, href))

    def _meta(self, values: dict[str, str | None]) -> None:
        content = values.get("content")
        if not content:
            return
        key = (values.get("property") or values.get("name") or "").lower()
        if key in ("article:modified_time", "og:updated_time") and self.modified is None:
            self.modified = content
        elif key in ("article:published_time", "og:published_time") and self.published is None:
            self.published = content
        elif key in ("robots", "googlebot"):
            self.robots.append(content)
        elif key == "og:image":
            self.images.append((content, ""))


def _robots_directives(header: str | None, meta: list[str]) -> set[str]:
    sources = [header or "", *meta]
    directives: set[str] = set()
    for source in sources:
        for piece in source.lower().split(","):
            token = piece.strip().split(":", 1)[0].strip()
            if token:
                directives.add(token)
    return directives


def _absolute_links(hrefs: list[str], base: str) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        absolute = _absolute_http(href, base)
        if absolute is None or absolute in seen:
            continue
        seen.add(absolute)
        found.append(absolute)
    return tuple(found)


def _absolute_http(href: str, base: str) -> str | None:
    absolute = urljoin(base, href.strip())
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https"):
        return None
    without_fragment = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    try:
        return normalize_url(without_fragment)
    except ValueError:
        return None


def _images(images: list[tuple[str, str]], base: str) -> tuple[ImageInfo, ...]:
    prepared: list[ImageInfo] = []
    seen: set[str] = set()
    for src, alt in images:
        loc = _absolute_http(src, base)
        if loc is None or loc in seen:
            continue
        seen.add(loc)
        prepared.append(ImageInfo(loc, alt or None))
        if len(prepared) >= _MAX_IMAGES:
            break
    return tuple(prepared)


def _alternates(alternates: list[tuple[str, str]], base: str) -> tuple[AlternateLink, ...]:
    prepared: list[AlternateLink] = []
    for hreflang, href in alternates:
        absolute = _absolute_http(href, base)
        if absolute is None:
            continue
        prepared.append(AlternateLink(hreflang.strip(), absolute))
        if len(prepared) >= _MAX_ALTERNATES:
            break
    return tuple(prepared)


def _accept_date(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return format_lastmod(value.strip())
    except ValueError:
        return None


def _http_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return format_lastmod(parsed)
    except ValueError:
        return None


def _jsonld_date(blocks: list[str], field: str) -> str | None:
    for block in blocks:
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in _jsonld_nodes(payload):
            found = _accept_date(node.get(field) if isinstance(node.get(field), str) else None)
            if found:
                return found
    return None


def _jsonld_nodes(payload: object) -> Iterator[dict[str, object]]:
    if isinstance(payload, list):
        for item in payload:
            yield from _jsonld_nodes(item)
        return
    if not isinstance(payload, dict):
        return
    yield payload
    graph = payload.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            yield from _jsonld_nodes(item)


def _srcset_first(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.split(",", 1)[0].strip().split()
    if not candidate:
        return None
    return candidate[0]


def _media(value: str | None) -> str:
    if not value:
        return ""
    return value.split(";", 1)[0].strip().lower()
