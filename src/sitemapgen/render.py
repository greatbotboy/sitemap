"""Render sitemap XML, sitemap indexes, and text URL lists."""

from __future__ import annotations

import gzip
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from xml.sax.saxutils import escape

from sitemapgen.models import (
    LIMIT_BYTES,
    LIMIT_URLS,
    BuildResult,
    DroppedUrl,
    FileKind,
    GeneratedFile,
    PreparedUrl,
    SitemapError,
    UrlEntry,
)
from sitemapgen.urls import directory_path, format_lastmod, normalize_url, prepare_entry

FormatName = Literal["xml", "txt"]

_URLSET_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
).encode("utf-8")
_URLSET_FOOTER = b"</urlset>\n"
_INDEX_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
).encode("utf-8")
_INDEX_FOOTER = b"</sitemapindex>\n"


def _urlset_header(images: bool, alternates: bool) -> bytes:
    if not images and not alternates:
        return _URLSET_HEADER
    attrs = ['xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"']
    if images:
        attrs.append('xmlns:image="http://www.google.com/schemas/sitemap-image/1.1"')
    if alternates:
        attrs.append('xmlns:xhtml="http://www.w3.org/1999/xhtml"')
    joined = "\n        ".join(attrs)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset {joined}>\n'.encode("utf-8")


def xml_escape(value: str) -> str:
    """Escape the five characters the sitemap protocol requires in data values."""

    return escape(value, {'"': "&quot;", "'": "&apos;"})


def build(
    entries: list[UrlEntry] | tuple[UrlEntry, ...],
    *,
    sitemap_url: str,
    fmt: FormatName = "xml",
    gzip: bool = False,
    max_urls: int = LIMIT_URLS,
    max_bytes: int = LIMIT_BYTES,
) -> BuildResult:
    """Validate entries and render one or more sitemap files.

    Size limits apply to the uncompressed UTF-8 document. Gzip, when requested,
    is applied after the split. A split result includes a sitemap index whose
    locations point at the part files.
    """

    if fmt not in ("xml", "txt"):
        raise SitemapError("format must be xml or txt")
    if isinstance(max_urls, bool) or not isinstance(max_urls, int) or max_urls < 1:
        raise SitemapError("max_urls must be a positive integer")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise SitemapError("max_bytes must be a positive integer")
    try:
        sitemap = normalize_url(sitemap_url)
    except ValueError as exc:
        raise SitemapError(str(exc)) from exc

    prepared: list[PreparedUrl] = []
    dropped: list[DroppedUrl] = []
    for entry in entries:
        item, reason = prepare_entry(entry, sitemap)
        if item is None:
            loc = entry.loc if isinstance(entry.loc, str) else ""
            dropped.append(DroppedUrl(loc, reason or "invalid URL"))
        else:
            prepared.append(item)

    if not prepared:
        return BuildResult((), tuple(dropped))

    kind: FileKind
    if fmt == "xml":
        chunks = [_render_url(item) for item in prepared]
        header = _urlset_header(
            any(item.images for item in prepared),
            any(item.alternates for item in prepared),
        )
        footer = _URLSET_FOOTER
        kind = "urlset"
        part_ext = ".xml"
    else:
        chunks = [_render_text(item.loc) for item in prepared]
        header, footer = b"", b""
        kind = "text"
        part_ext = ".txt"

    groups, skipped = _pack(chunks, header, footer, max_urls, max_bytes)
    for index in skipped:
        dropped.append(
            DroppedUrl(prepared[index].loc, "URL entry exceeds the maximum sitemap size")
        )
    if not groups:
        return BuildResult((), tuple(dropped))

    stem, single_name, index_name = _filenames(sitemap, fmt)
    if len(groups) == 1:
        raw = header + b"".join(groups[0]) + footer
        public = _with_filename(sitemap, single_name)
        generated = _finish(single_name, raw, public, kind, gzip)
        return BuildResult((generated,), tuple(dropped))

    parts: list[GeneratedFile] = []
    for number, group in enumerate(groups, start=1):
        raw = header + b"".join(group) + footer
        name = f"{stem}-{number}{part_ext}"
        public = _with_filename(sitemap, name)
        parts.append(_finish(name, raw, public, kind, gzip))

    generated_at = format_lastmod(datetime.now(timezone.utc).replace(microsecond=0))
    index_chunks = [_render_index_entry(part.public_url, generated_at) for part in parts]
    index_groups, index_skipped = _pack(
        index_chunks, _INDEX_HEADER, _INDEX_FOOTER, max_urls, max_bytes
    )
    if index_skipped or not index_groups:
        raise SitemapError("sitemap index entry exceeds the maximum sitemap size")

    indexes: list[GeneratedFile] = []
    if len(index_groups) == 1:
        raw = _INDEX_HEADER + b"".join(index_groups[0]) + _INDEX_FOOTER
        public = _with_filename(sitemap, index_name)
        indexes.append(_finish(index_name, raw, public, "index", gzip))
    else:
        for number, group in enumerate(index_groups, start=1):
            raw = _INDEX_HEADER + b"".join(group) + _INDEX_FOOTER
            name = f"{stem}-index-{number}.xml"
            public = _with_filename(sitemap, name)
            indexes.append(_finish(name, raw, public, "index", gzip))

    return BuildResult(tuple([*parts, *indexes]), tuple(dropped))


def write(result: BuildResult, output_dir: str | Path) -> list[Path]:
    """Write every generated file into ``output_dir``, creating it if needed."""

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for item in result.files:
        if item.name != Path(item.name).name or item.name in ("", ".", ".."):
            raise SitemapError(f"unsafe sitemap filename: {item.name}")
        path = directory / item.name
        path.write_bytes(item.content)
        written.append(path)
    return written


def _render_url(item: PreparedUrl) -> bytes:
    lines = ["  <url>", f"    <loc>{xml_escape(item.loc)}</loc>"]
    if item.lastmod is not None:
        lines.append(f"    <lastmod>{xml_escape(item.lastmod)}</lastmod>")
    if item.changefreq is not None:
        lines.append(f"    <changefreq>{xml_escape(item.changefreq)}</changefreq>")
    if item.priority is not None:
        lines.append(f"    <priority>{xml_escape(item.priority)}</priority>")
    for alternate in item.alternates:
        lines.append(
            "    <xhtml:link"
            ' rel="alternate"'
            f' hreflang="{xml_escape(alternate.hreflang)}"'
            f' href="{xml_escape(alternate.href)}"/>'
        )
    for image in item.images:
        lines.append("    <image:image>")
        lines.append(f"      <image:loc>{xml_escape(image.loc)}</image:loc>")
        if image.title:
            lines.append(f"      <image:title>{xml_escape(image.title)}</image:title>")
        lines.append("    </image:image>")
    lines.append("  </url>")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _render_text(loc: str) -> bytes:
    return (loc + "\n").encode("utf-8")


def _render_index_entry(loc: str, lastmod: str) -> bytes:
    text = (
        "  <sitemap>\n"
        f"    <loc>{xml_escape(loc)}</loc>\n"
        f"    <lastmod>{xml_escape(lastmod)}</lastmod>\n"
        "  </sitemap>\n"
    )
    return text.encode("utf-8")


def _pack(
    chunks: list[bytes],
    header: bytes,
    footer: bytes,
    max_urls: int,
    max_bytes: int,
) -> tuple[list[list[bytes]], list[int]]:
    groups: list[list[bytes]] = []
    current: list[bytes] = []
    size = len(header) + len(footer)
    skipped: list[int] = []
    for index, chunk in enumerate(chunks):
        if len(header) + len(chunk) + len(footer) > max_bytes:
            skipped.append(index)
            continue
        if current and (len(current) >= max_urls or size + len(chunk) > max_bytes):
            groups.append(current)
            current = []
            size = len(header) + len(footer)
        current.append(chunk)
        size += len(chunk)
    if current:
        groups.append(current)
    return groups, skipped


def _filenames(sitemap_url: str, fmt: FormatName) -> tuple[str, str, str]:
    filename = urlsplit(sitemap_url).path.rsplit("/", 1)[-1]
    if filename.endswith(".gz"):
        filename = filename[:-3]
    if not filename:
        filename = "sitemap.xml"
    if "." in filename:
        stem, ext = filename.rsplit(".", 1)
        ext = "." + ext.lower()
    else:
        stem, ext = filename, ""
    if not stem or stem in (".", ".."):
        stem = "sitemap"
    part_ext = ".txt" if fmt == "txt" else ".xml"
    if (fmt == "xml" and ext == ".xml") or (fmt == "txt" and ext == ".txt"):
        single = stem + ext
    else:
        single = stem + part_ext
    return stem, single, stem + ".xml"


def _with_filename(sitemap_url: str, filename: str) -> str:
    parts = urlsplit(sitemap_url)
    path = directory_path(parts.path) + filename
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _finish(
    name: str,
    raw: bytes,
    public_url: str,
    kind: FileKind,
    gzip_output: bool,
) -> GeneratedFile:
    if gzip_output:
        return GeneratedFile(name + ".gz", _gzip(raw), public_url + ".gz", kind)
    return GeneratedFile(name, raw, public_url, kind)


def _gzip(data: bytes) -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", filename="", mtime=0) as handle:
        handle.write(data)
    return buffer.getvalue()
