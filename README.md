# sitemapgen

Generate [Sitemaps protocol](https://www.sitemaps.org/protocol.html) files from a list of URLs or by crawling a site.

The package is stdlib-only and targets Python 3.11+. It writes XML urlsets, sitemap index files when a document would pass the protocol limits, and the plain-text URL format. It can also add a `Sitemap:` line to `robots.txt`.

RSS, Atom, namespace extensions, and search-engine ping are out of scope.

## Install

```bash
pip install -e ".[dev]"
```

## CLI

Build from a text file with one absolute URL per line:

```bash
sitemapgen build \
  --sitemap-url https://example.com/sitemap.xml \
  --urls urls.txt \
  --output ./out
```

Build from JSONL. Each line is an object with `loc` and optional `lastmod`, `changefreq`, and `priority`:

```bash
sitemapgen build \
  --sitemap-url https://example.com/sitemap.xml \
  --jsonl entries.jsonl \
  --output ./out \
  --gzip \
  --robots ./out/robots.txt
```

Crawl one host and write the sitemap. The default sitemap URL is `{origin}/sitemap.xml`.

```bash
sitemapgen crawl https://example.com/ --output ./out --max-pages 500 --delay 0.2
```

`--format txt` writes one absolute URL per line. A split text result still uses an XML sitemap index, which the protocol allows. `--gzip` compresses each file after the size split; index locations use the `.gz` name. URLs that break the protocol are printed to stderr and left out of the file. The process exits non-zero when the input cannot be read or no valid URL remains.

## Library

```python
from datetime import date
from sitemapgen import UrlEntry, build, crawl, upsert_sitemap_directive, write

entries = [
    UrlEntry(
        "https://example.com/",
        lastmod=date(2024, 1, 1),
        changefreq="weekly",
        priority=0.8,
    )
]
result = build(entries, sitemap_url="https://example.com/sitemap.xml")
write(result, "./out")

pages = crawl("https://example.com/", max_pages=100)
robots = upsert_sitemap_directive("", result.advertised_urls[0])
```

`result.dropped` lists URLs that were left out and why. `result.advertised_urls` is the index URL when the output is split, otherwise the single file URL.

Pass `max_urls` and `max_bytes` to `build` to override the protocol caps of 50,000 URLs and 52,428,800 uncompressed bytes. Those arguments exist so callers can test splitting; leave them at the defaults for real files.

## Rules enforced

- Documents are UTF-8. XML declares that encoding and uses the `http://www.sitemaps.org/schemas/sitemap/0.9` namespace.
- `loc` is required, must be an absolute `http` or `https` URL, and must be shorter than 2,048 characters.
- Non-ASCII characters are percent-encoded as UTF-8. Query separators stay in place, and `&`, `'`, `"`, `>`, and `<` are XML-escaped in element text.
- `lastmod` is a W3C datetime. A `date` is written as `YYYY-MM-DD`. A `datetime` is written with an offset; a naive value is treated as UTC. This is the page modification time you supply, not the time the file is generated.
- `changefreq` is `always`, `hourly`, `daily`, `weekly`, `monthly`, `yearly`, or `never`. It is omitted unless you set it. Search engines treat it as a hint, so the crawler does not invent one.
- `priority` is from `0.0` to `1.0` and is omitted when unset. The protocol default is `0.5`.
- A crawl adds `lastmod` from the page's modified time, then the `Last-Modified` header, then a published time. It adds `image:image` entries (URL and alt text) and `xhtml:link` hreflang alternates when the page has them. The sitemap `loc` is the canonical URL when that URL is on the same host. Pages with `noindex` are left out.
- Every page URL must use the sitemap's scheme, host, and port, and must live under the sitemap file's directory. If the sitemap URL has an explicit port, each page URL must include that port.
- A file stops before it would exceed 50,000 URLs or 50 MiB uncompressed. Further URLs go into `sitemap-1.xml`, `sitemap-2.xml`, and so on. `sitemap.xml` is then a sitemap index. Index `<lastmod>` is when that part was generated. The same caps apply to the index.
- The text format is UTF-8, one absolute URL per line, with no header or footer, and the same count and size caps.
- `upsert_sitemap_directive` appends `Sitemap: {absolute url}` without changing other lines. When several index files are required, each one gets a directive.

The crawler stays on the start URL's scheme, host, and port, strips fragments, follows in-origin redirects, and keeps HTML responses. It reads `robots.txt` with `urllib.robotparser` unless you pass `--ignore-robots`.

JSONL images and alternates look like this:

```json
{"loc": "https://example.com/guide", "lastmod": "2024-05-01", "images": [{"loc": "https://cdn.example.com/cover.jpg", "title": "Cover"}], "alternates": [{"hreflang": "de", "href": "https://example.de/guide"}]}
```
