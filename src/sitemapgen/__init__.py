"""Generate Sitemaps protocol files."""

from sitemapgen.crawl import crawl
from sitemapgen.models import (
    LIMIT_BYTES,
    LIMIT_URLS,
    AlternateLink,
    BuildResult,
    ChangeFreq,
    DroppedUrl,
    GeneratedFile,
    ImageInfo,
    SitemapError,
    UrlEntry,
)
from sitemapgen.render import build, write
from sitemapgen.robots import upsert_sitemap_directive

__version__ = "0.1.0"

__all__ = [
    "LIMIT_BYTES",
    "LIMIT_URLS",
    "AlternateLink",
    "BuildResult",
    "ChangeFreq",
    "DroppedUrl",
    "GeneratedFile",
    "ImageInfo",
    "SitemapError",
    "UrlEntry",
    "build",
    "crawl",
    "upsert_sitemap_directive",
    "write",
]
