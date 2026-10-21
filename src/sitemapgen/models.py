"""Shared types and protocol limits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Literal

# https://www.sitemaps.org/protocol.html
# Each sitemap or index holds at most 50,000 URLs and 50 MiB uncompressed.
LIMIT_URLS = 50_000
LIMIT_BYTES = 52_428_800
LOC_MAX = 2_048
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

FileKind = Literal["urlset", "text", "index"]


class SitemapError(ValueError):
    """The sitemap could not be built or written."""


class ChangeFreq(StrEnum):
    ALWAYS = "always"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"
    NEVER = "never"


@dataclass(frozen=True)
class ImageInfo:
    """One image belonging to a page.

    ``loc`` may be on another host, such as a CDN. ``title`` comes from the
    image alt text when the crawler can see one.
    """

    loc: str
    title: str | None = None


@dataclass(frozen=True)
class AlternateLink:
    """An ``xhtml:link`` hreflang alternate for a page."""

    hreflang: str
    href: str


@dataclass(frozen=True)
class UrlEntry:
    """One page in a sitemap.

    ``lastmod`` is the date the page changed. It is not the time the sitemap
    file is generated. ``changefreq`` and ``priority`` are included only when
    the caller supplies them.
    """

    loc: str
    lastmod: date | datetime | str | None = None
    changefreq: ChangeFreq | str | None = None
    priority: float | None = None
    images: tuple[ImageInfo, ...] = ()
    alternates: tuple[AlternateLink, ...] = ()


@dataclass(frozen=True)
class PreparedImage:
    loc: str
    title: str | None


@dataclass(frozen=True)
class PreparedAlternate:
    hreflang: str
    href: str


@dataclass(frozen=True)
class PreparedUrl:
    loc: str
    lastmod: str | None
    changefreq: str | None
    priority: str | None
    images: tuple[PreparedImage, ...] = ()
    alternates: tuple[PreparedAlternate, ...] = ()


@dataclass(frozen=True)
class DroppedUrl:
    loc: str
    reason: str


@dataclass(frozen=True)
class GeneratedFile:
    """Bytes to write and the absolute URL where that file will be hosted."""

    name: str
    content: bytes
    public_url: str
    kind: FileKind


@dataclass(frozen=True)
class BuildResult:
    files: tuple[GeneratedFile, ...]
    dropped: tuple[DroppedUrl, ...]

    @property
    def advertised_urls(self) -> tuple[str, ...]:
        """URLs to list from robots.txt.

        When the build is split, these are the sitemap index files.
        A single output file is advertised directly.
        """

        indexes = tuple(item.public_url for item in self.files if item.kind == "index")
        if indexes:
            return indexes
        return tuple(item.public_url for item in self.files)
