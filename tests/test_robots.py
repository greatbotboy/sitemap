"""robots.txt Sitemap directives."""

from sitemapgen import upsert_sitemap_directive


def test_appends_one_sitemap_line_and_keeps_existing_rules() -> None:
    original = "User-agent: *\nDisallow: /private\nSitemap: https://example.com/other.xml\n"
    updated = upsert_sitemap_directive(original, "https://example.com/sitemap.xml")
    assert updated == (
        "User-agent: *\n"
        "Disallow: /private\n"
        "Sitemap: https://example.com/other.xml\n"
        "Sitemap: https://example.com/sitemap.xml\n"
    )
    assert upsert_sitemap_directive(updated, "https://example.com/sitemap.xml") == updated


def test_empty_file_and_crlf_are_preserved() -> None:
    assert upsert_sitemap_directive("", "https://example.com/sitemap.xml") == (
        "Sitemap: https://example.com/sitemap.xml\n"
    )
    original = "User-agent: *\r\nDisallow: /tmp"
    updated = upsert_sitemap_directive(original, "https://example.com/sitemap.xml")
    assert updated == (
        "User-agent: *\r\nDisallow: /tmp\r\nSitemap: https://example.com/sitemap.xml\r\n"
    )
