"""SEO fields: lastmod, images, hreflang, and noindex."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from sitemapgen import AlternateLink, ImageInfo, UrlEntry, build, crawl
from sitemapgen.page import extract_page

SITEMAP = "http://example.com/sitemap.xml"


def test_xml_includes_images_and_hreflang() -> None:
    result = build(
        [
            UrlEntry(
                "http://example.com/guide",
                lastmod="2024-05-01",
                images=(
                    ImageInfo("https://cdn.example.com/cover.jpg", 'Hero & "sky"'),
                    ImageInfo("not a url"),
                ),
                alternates=(
                    AlternateLink("de", "https://example.de/guide"),
                    AlternateLink("bogus tag", "https://example.com/nope"),
                    AlternateLink("x-default", "http://example.com/guide"),
                ),
            )
        ],
        sitemap_url=SITEMAP,
    )
    body = result.files[0].content.decode()
    assert 'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1"' in body
    assert 'xmlns:xhtml="http://www.w3.org/1999/xhtml"' in body
    assert "<lastmod>2024-05-01</lastmod>" in body
    assert "<image:loc>https://cdn.example.com/cover.jpg</image:loc>" in body
    assert "<image:title>Hero &amp; &quot;sky&quot;</image:title>" in body
    assert 'hreflang="de"' in body
    assert 'href="https://example.de/guide"' in body
    assert 'hreflang="x-default"' in body
    assert "bogus" not in body
    assert "<changefreq>" not in body
    assert "<priority>" not in body
    assert not result.dropped


def test_plain_urlset_omits_extension_namespaces() -> None:
    body = build([UrlEntry("http://example.com/")], sitemap_url=SITEMAP).files[0].content.decode()
    assert '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' in body
    assert "xmlns:image" not in body
    assert "xmlns:xhtml" not in body


def test_text_format_stays_one_url_per_line() -> None:
    result = build(
        [UrlEntry("http://example.com/a", images=(ImageInfo("http://example.com/a.jpg"),))],
        sitemap_url=SITEMAP,
        fmt="txt",
    )
    assert result.files[0].content == b"http://example.com/a\n"


def test_extract_prefers_modified_time_and_skips_noindex() -> None:
    html = """
    <html><head>
      <link rel="canonical" href="/guide">
      <meta property="article:modified_time" content="2024-05-01">
      <meta name="robots" content="noindex, nofollow">
      <link rel="alternate" hreflang="de" href="https://example.de/guide">
      <meta property="og:image" content="https://cdn.example.com/cover.jpg">
    </head><body>
      <a href="/next">Next</a>
      <img src="/photos/hero.jpg" alt="Hero &amp; sky">
      <script type="application/ld+json">{"dateModified": "2020-01-01"}</script>
    </body></html>
    """
    facts = extract_page(
        html,
        "http://example.com/copy",
        last_modified="Tue, 01 Jan 2019 00:00:00 GMT",
    )
    assert facts.include is False
    assert facts.links == ("http://example.com/guide",)
    assert facts.loc == "http://example.com/guide"
    assert facts.lastmod == "2024-05-01"
    assert facts.images[0].loc == "https://cdn.example.com/cover.jpg"
    assert facts.images[1].loc == "http://example.com/photos/hero.jpg"
    assert facts.images[1].title == "Hero & sky"
    assert facts.alternates == (AlternateLink("de", "https://example.de/guide"),)


def test_extract_uses_jsonld_and_header_fallbacks() -> None:
    published = extract_page(
        '<html><head><script type="application/ld+json">'
        '{"@graph": [{"datePublished": "2023-03-03"}]}'
        "</script></head></html>",
        "http://example.com/",
    )
    assert published.lastmod == "2023-03-03"
    header = extract_page(
        "<html><head></head></html>",
        "http://example.com/",
        last_modified="Tue, 01 Jan 2019 00:00:00 GMT",
    )
    assert header.lastmod == "2019-01-01T00:00:00+00:00"
    assert published.include is True

    offsite = extract_page(
        '<html><head><link rel="canonical" href="https://other.example/page"></head></html>',
        "http://example.com/page",
    )
    assert offsite.include is False


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/robots.txt":
            body = b"User-agent: *\nDisallow:\n"
            content_type = "text/plain"
            last_modified = None
        elif path == "/copy":
            body = (
                b"<html><head>"
                b'<link rel="canonical" href="/guide">'
                b'<meta name="robots" content="nofollow">'
                b"</head><body><a href='/skip'>Skip</a></body></html>"
            )
            content_type = "text/html"
            last_modified = None
        elif path == "/guide":
            body = (
                b"<html><head>"
                b'<meta property="article:modified_time" content="2024-05-01">'
                b'<link rel="alternate" hreflang="de" href="https://example.de/guide">'
                b'<link rel="alternate" hreflang="en" href="/guide">'
                b"</head><body>"
                b'<img src="/photos/hero.jpg" alt="Hero">'
                b'<a href="/hidden">Hidden</a>'
                b"</body></html>"
            )
            content_type = "text/html"
            last_modified = "Tue, 01 Jan 2020 00:00:00 GMT"
        elif path == "/hidden":
            body = b'<html><head><meta name="robots" content="noindex"></head></html>'
            content_type = "text/html"
            last_modified = None
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if last_modified:
            self.send_header("Last-Modified", last_modified)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


def test_crawl_records_canonical_page_details() -> None:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        entries = crawl(f"http://127.0.0.1:{port}/copy", max_pages=10)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.loc == f"http://127.0.0.1:{port}/guide"
    assert entry.lastmod == "2024-05-01"
    assert entry.changefreq is None
    assert entry.priority is None
    assert entry.images == (ImageInfo(f"http://127.0.0.1:{port}/photos/hero.jpg", "Hero"),)
    assert entry.alternates == (
        AlternateLink("de", "https://example.de/guide"),
        AlternateLink("en", f"http://127.0.0.1:{port}/guide"),
    )
