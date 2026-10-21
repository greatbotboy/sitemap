"""XML and text rendering, splits, and gzip."""

import gzip
import re
from datetime import date

from sitemapgen import UrlEntry, build, write
from sitemapgen.render import xml_escape

SITEMAP = "http://example.com/sitemap.xml"
_BARE_AMPERSAND = re.compile(r"&(?!amp;|apos;|quot;|gt;|lt;)")
_W3C = re.compile(
    r"^\d{4}(-\d{2}(-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2}))?)?)?$"
)


def test_xml_declaration_namespace_and_escaping() -> None:
    result = build(
        [
            UrlEntry("http://www.example.com/ümlat"),
            UrlEntry("http://www.example.com/catalog?item=12&desc=vacation_hawaii"),
        ],
        sitemap_url="http://www.example.com/sitemap.xml",
    )
    body = result.files[0].content.decode()
    assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>\n')
    assert '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' in body
    assert "http://www.example.com/%C3%BCmlat" in body
    assert "ü" not in body
    assert "item=12&amp;desc=vacation_hawaii" in body
    assert _BARE_AMPERSAND.search(body) is None
    assert result.files[0].name == "sitemap.xml"
    assert result.advertised_urls == ("http://www.example.com/sitemap.xml",)


def test_xml_escape_quotes_and_brackets() -> None:
    assert xml_escape("""&<>"'""") == "&amp;&lt;&gt;&quot;&apos;"


def test_omits_optional_tags() -> None:
    body = build([UrlEntry("http://example.com/")], sitemap_url=SITEMAP).files[0].content.decode()
    assert "<lastmod>" not in body
    assert "<changefreq>" not in body
    assert "<priority>" not in body


def test_splits_on_max_urls_and_points_the_index_at_parts() -> None:
    entries = [UrlEntry(f"http://example.com/{number}") for number in range(3)]
    result = build(entries, sitemap_url=SITEMAP, max_urls=2)
    kinds = [item.kind for item in result.files]
    assert kinds == ["urlset", "urlset", "index"]
    assert [item.name for item in result.files] == [
        "sitemap-1.xml",
        "sitemap-2.xml",
        "sitemap.xml",
    ]
    index = result.files[2].content.decode()
    assert "<sitemapindex" in index
    assert "http://example.com/sitemap-1.xml" in index
    assert "http://example.com/sitemap-2.xml" in index
    lastmod = index.split("<lastmod>", 1)[1].split("</lastmod>", 1)[0]
    assert _W3C.fullmatch(lastmod)
    assert result.advertised_urls == ("http://example.com/sitemap.xml",)
    assert b"http://example.com/0" in result.files[0].content
    assert b"http://example.com/2" in result.files[1].content


def test_splits_on_uncompressed_size() -> None:
    short = build([UrlEntry("http://example.com/a")], sitemap_url=SITEMAP)
    size = len(short.files[0].content)
    long_loc = "http://example.com/" + ("x" * 400)
    result = build(
        [UrlEntry(long_loc), UrlEntry("http://example.com/a")],
        sitemap_url=SITEMAP,
        max_bytes=size,
    )
    assert [item.loc for item in result.dropped] == [long_loc]
    assert result.dropped[0].reason == "URL entry exceeds the maximum sitemap size"
    assert len(result.files) == 1
    assert b"http://example.com/a" in result.files[0].content

    # Long page URLs make each urlset larger than the index that points at the parts,
    # so the same byte cap splits the urlset and still leaves room for the index.
    loc_a = "http://example.com/" + ("a" * 500)
    loc_b = "http://example.com/" + ("b" * 500)
    one_long = build([UrlEntry(loc_a)], sitemap_url=SITEMAP)
    long_size = len(one_long.files[0].content)
    split = build(
        [UrlEntry(loc_a), UrlEntry(loc_b)],
        sitemap_url=SITEMAP,
        max_bytes=long_size,
    )
    assert [item.kind for item in split.files] == ["urlset", "urlset", "index"]
    assert len(split.files[0].content) <= long_size
    assert len(split.files[1].content) <= long_size
    assert len(split.files[2].content) <= long_size


def test_gzip_round_trip_uses_gz_names_in_the_index() -> None:
    result = build(
        [UrlEntry("http://example.com/a"), UrlEntry("http://example.com/b")],
        sitemap_url=SITEMAP,
        max_urls=1,
        gzip=True,
    )
    assert [item.name for item in result.files] == [
        "sitemap-1.xml.gz",
        "sitemap-2.xml.gz",
        "sitemap-index-1.xml.gz",
        "sitemap-index-2.xml.gz",
    ]
    first = gzip.decompress(result.files[0].content)
    assert first.startswith(b"<?xml")
    assert b"http://example.com/a" in first
    index = gzip.decompress(result.files[2].content).decode()
    assert "http://example.com/sitemap-1.xml.gz" in index
    assert all(url.endswith(".gz") for url in result.advertised_urls)


def test_text_format_has_no_header_and_uses_an_xml_index_when_split() -> None:
    single = build(
        [
            UrlEntry("http://example.com/a"),
            UrlEntry("http://example.com/b?x=1&y=2"),
        ],
        sitemap_url=SITEMAP,
        fmt="txt",
    )
    assert single.files[0].name == "sitemap.txt"
    assert single.files[0].content == b"http://example.com/a\nhttp://example.com/b?x=1&y=2\n"
    assert not single.files[0].content.startswith(b"<?xml")

    split = build(
        [UrlEntry(f"http://example.com/{number}") for number in range(3)],
        sitemap_url=SITEMAP,
        fmt="txt",
        max_urls=2,
    )
    assert [item.name for item in split.files] == [
        "sitemap-1.txt",
        "sitemap-2.txt",
        "sitemap.xml",
    ]
    assert split.files[0].content == b"http://example.com/0\nhttp://example.com/1\n"
    assert b"<loc>http://example.com/sitemap-1.txt</loc>" in split.files[2].content
    assert split.advertised_urls == ("http://example.com/sitemap.xml",)


def test_write_creates_the_output_directory(tmp_path) -> None:
    result = build(
        [UrlEntry("http://example.com/", lastmod=date(2024, 1, 1))],
        sitemap_url=SITEMAP,
    )
    output = tmp_path / "nested" / "out"
    written = write(result, output)
    assert written == [output / "sitemap.xml"]
    assert written[0].read_bytes() == result.files[0].content


def test_protocol_limits_are_the_defaults() -> None:
    from sitemapgen import LIMIT_BYTES, LIMIT_URLS

    assert LIMIT_URLS == 50_000
    assert LIMIT_BYTES == 52_428_800
