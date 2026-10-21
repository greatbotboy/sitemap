"""robots.txt Sitemap directives."""

from __future__ import annotations

from sitemapgen.models import SitemapError


def upsert_sitemap_directive(robots_text: str, sitemap_url: str) -> str:
    """Add ``Sitemap: {sitemap_url}`` when that exact directive is missing.

    Existing lines, including other Sitemap directives, are left unchanged.
    """

    if not isinstance(robots_text, str):
        raise SitemapError("robots.txt text must be a string")
    if not isinstance(sitemap_url, str) or not sitemap_url.startswith(("http://", "https://")):
        raise SitemapError("sitemap URL must be absolute")
    directive = f"Sitemap: {sitemap_url}"
    for line in robots_text.splitlines():
        if line.strip() == directive:
            return robots_text
    newline = "\r\n" if "\r\n" in robots_text else "\n"
    if robots_text == "":
        return directive + newline
    if robots_text.endswith(("\n", "\r\n")):
        return robots_text + directive + newline
    return robots_text + newline + directive + newline
