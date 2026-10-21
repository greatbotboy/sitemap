"""URL normalization, location rules, and metadata."""

from datetime import date, datetime, timedelta, timezone

from sitemapgen import UrlEntry, build
from sitemapgen.urls import normalize_url

SITEMAP = "http://example.com/sitemap.xml"


def test_percent_encodes_non_ascii_without_double_encoding() -> None:
    normalized = normalize_url("http://www.example.com/ümlat")
    assert normalized == "http://www.example.com/%C3%BCmlat"
    again = normalize_url(normalized)
    assert again == normalized

    host = "bücher.example".encode("idna").decode("ascii")
    assert host in normalize_url("http://bücher.example/path")


def test_encodes_spaces_and_preserves_query_separators() -> None:
    normalized = normalize_url("http://example.com/a b?item=12&desc=vacation+hawaii")
    assert normalized == "http://example.com/a%20b?item=12&desc=vacation+hawaii"


def test_rejects_bad_urls() -> None:
    result = build(
        [
            UrlEntry("ftp://example.com/"),
            UrlEntry("http://example.com/" + ("a" * 2048)),
            UrlEntry("http://example.com/ok"),
        ],
        sitemap_url=SITEMAP,
    )
    assert [item.loc for item in result.dropped] == [
        "ftp://example.com/",
        "http://example.com/" + ("a" * 2048),
    ]
    assert "less than 2048" in result.dropped[1].reason
    assert b"http://example.com/ok" in result.files[0].content


def test_location_rules() -> None:
    catalog = "http://example.com/catalog/sitemap.xml"
    kept = build(
        [UrlEntry("http://example.com/catalog/show?item=23")],
        sitemap_url=catalog,
    )
    assert not kept.dropped

    dropped = build(
        [
            UrlEntry("http://example.com/image/show"),
            UrlEntry("https://example.com/catalog/page"),
            UrlEntry("http://other.example/catalog/page"),
            UrlEntry("http://example.com:100/catalog/page"),
        ],
        sitemap_url=catalog,
    )
    reasons = [item.reason for item in dropped.dropped]
    assert reasons == [
        "URL is outside the sitemap directory",
        "URL scheme does not match the sitemap",
        "URL host does not match the sitemap",
        "URL port does not match the sitemap",
    ]
    assert dropped.files == ()


def test_explicit_port_must_be_repeated() -> None:
    sitemap = "http://www.example.com:100/sitemap.xml"
    missing = build([UrlEntry("http://www.example.com/page")], sitemap_url=sitemap)
    assert missing.dropped[0].reason == "URL must include port 100"

    present = build([UrlEntry("http://www.example.com:100/page")], sitemap_url=sitemap)
    assert not present.dropped
    assert b"http://www.example.com:100/page" in present.files[0].content


def test_default_port_matches_an_omitted_port() -> None:
    result = build(
        [UrlEntry("https://example.com:443/page")],
        sitemap_url="https://example.com/sitemap.xml",
    )
    assert not result.dropped


def test_metadata_validation() -> None:
    valid = build(
        [
            UrlEntry(
                "http://example.com/",
                lastmod=date(2005, 1, 1),
                changefreq="monthly",
                priority=0.8,
            ),
            UrlEntry(
                "http://example.com/when",
                lastmod=datetime(2004, 12, 23, 18, 0, 15, tzinfo=timezone.utc),
                priority=1,
            ),
            UrlEntry("http://example.com/year", lastmod="1997"),
            UrlEntry("http://example.com/month", lastmod="1997-07"),
            UrlEntry("http://example.com/leap", lastmod="2016-02-29"),
            UrlEntry(
                "http://example.com/offset",
                lastmod=datetime(
                    2004, 12, 23, 18, 0, 15, tzinfo=timezone(timedelta(hours=-5))
                ),
            ),
        ],
        sitemap_url=SITEMAP,
    )
    body = valid.files[0].content.decode()
    assert "<lastmod>2005-01-01</lastmod>" in body
    assert "<lastmod>2004-12-23T18:00:15+00:00</lastmod>" in body
    assert "<lastmod>1997</lastmod>" in body
    assert "<lastmod>1997-07</lastmod>" in body
    assert "<lastmod>2016-02-29</lastmod>" in body
    assert "<lastmod>2004-12-23T18:00:15-05:00</lastmod>" in body
    assert "<changefreq>monthly</changefreq>" in body
    assert "<priority>0.8</priority>" in body
    assert "<priority>1.0</priority>" in body
    assert body.count("<priority>") == 2

    invalid = build(
        [
            UrlEntry("http://example.com/a", changefreq="Daily"),
            UrlEntry("http://example.com/b", priority=1.1),
            UrlEntry("http://example.com/c", priority=True),  # type: ignore[arg-type]
            UrlEntry("http://example.com/d", lastmod="2015-02-29"),
            UrlEntry("http://example.com/e", lastmod="yesterday"),
            UrlEntry("http://example.com/f", lastmod="2005-01-01T25:00:00Z"),
        ],
        sitemap_url=SITEMAP,
    )
    assert invalid.files == ()
    assert len(invalid.dropped) == 6
    assert "changefreq" in invalid.dropped[0].reason
    assert "priority" in invalid.dropped[1].reason
    assert "lastmod" in invalid.dropped[3].reason
