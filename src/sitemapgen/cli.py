"""Command-line interface for sitemapgen."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sitemapgen.crawl import crawl
from sitemapgen.models import AlternateLink, ImageInfo, SitemapError, UrlEntry
from sitemapgen.render import build, write
from sitemapgen.robots import upsert_sitemap_directive
from sitemapgen.urls import normalize_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sitemapgen",
        description="Generate Sitemaps protocol files from URLs or by crawling a site.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build_parser = sub.add_parser("build", help="write a sitemap from a URL list")
    build_parser.add_argument("--sitemap-url", required=True, help="absolute URL where the sitemap will be hosted")
    build_parser.add_argument("--output", required=True, type=Path, help="directory for the generated files")
    source = build_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--urls", type=Path, help="text file with one absolute URL per line")
    source.add_argument(
        "--jsonl",
        type=Path,
        help="JSONL file of loc, lastmod, changefreq, priority, images, and alternates",
    )
    _add_output_args(build_parser)

    crawl_parser = sub.add_parser("crawl", help="crawl a site and write a sitemap")
    crawl_parser.add_argument("start_url", help="absolute URL to start from")
    crawl_parser.add_argument("--output", required=True, type=Path, help="directory for the generated files")
    crawl_parser.add_argument(
        "--sitemap-url",
        help="absolute URL where the sitemap will be hosted (default: {origin}/sitemap.xml)",
    )
    crawl_parser.add_argument("--max-pages", type=int, help="stop after this many HTML pages")
    crawl_parser.add_argument("--delay", type=float, default=0.0, help="seconds to wait between requests")
    crawl_parser.add_argument(
        "--ignore-robots",
        action="store_true",
        help="crawl paths disallowed by robots.txt",
    )
    _add_output_args(crawl_parser)

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            entries = _load_urls(args.urls) if args.urls else _load_jsonl(args.jsonl)
            result = build(
                entries,
                sitemap_url=args.sitemap_url,
                fmt=args.format,
                gzip=args.gzip,
            )
        else:
            sitemap_url = args.sitemap_url or _default_sitemap_url(args.start_url)
            entries = crawl(
                args.start_url,
                max_pages=args.max_pages,
                delay=args.delay,
                respect_robots=not args.ignore_robots,
            )
            result = build(
                entries,
                sitemap_url=sitemap_url,
                fmt=args.format,
                gzip=args.gzip,
            )
    except (SitemapError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 1

    for item in result.dropped:
        print(f"dropped {item.loc}: {item.reason}", file=sys.stderr)
    if not result.files:
        print("no valid URLs to write", file=sys.stderr)
        return 1

    try:
        for path in write(result, args.output):
            print(path)
        if args.robots is not None:
            _update_robots(args.robots, result.advertised_urls)
            print(args.robots)
    except (SitemapError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def _add_output_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("xml", "txt"), default="xml")
    parser.add_argument("--gzip", action="store_true", help="gzip each output file")
    parser.add_argument("--robots", type=Path, help="add a Sitemap directive to this robots.txt file")


def _load_urls(path: Path) -> list[UrlEntry]:
    entries: list[UrlEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        loc = line.strip()
        if loc:
            entries.append(UrlEntry(loc))
    return entries


def _load_jsonl(path: Path) -> list[UrlEntry]:
    entries: list[UrlEntry] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SitemapError(f"{path}:{lineno}: invalid JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("loc"), str):
            raise SitemapError(f"{path}:{lineno}: expected an object with a loc string")
        entries.append(
            UrlEntry(
                loc=data["loc"],
                lastmod=data.get("lastmod"),
                changefreq=data.get("changefreq"),
                priority=data.get("priority"),
                images=_load_images(data.get("images"), path, lineno),
                alternates=_load_alternates(data.get("alternates"), path, lineno),
            )
        )
    return entries


def _load_images(value: object, path: Path, lineno: int) -> tuple[ImageInfo, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SitemapError(f"{path}:{lineno}: images must be a list")
    images: list[ImageInfo] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict) or not isinstance(item.get("loc"), str):
            raise SitemapError(f"{path}:{lineno}: image {index} needs a loc string")
        title = item.get("title")
        if title is not None and not isinstance(title, str):
            raise SitemapError(f"{path}:{lineno}: image {index} title must be a string")
        images.append(ImageInfo(item["loc"], title))
    return tuple(images)


def _load_alternates(value: object, path: Path, lineno: int) -> tuple[AlternateLink, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SitemapError(f"{path}:{lineno}: alternates must be a list")
    alternates: list[AlternateLink] = []
    for index, item in enumerate(value, start=1):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("hreflang"), str)
            or not isinstance(item.get("href"), str)
        ):
            raise SitemapError(
                f"{path}:{lineno}: alternate {index} needs hreflang and href strings"
            )
        alternates.append(AlternateLink(item["hreflang"], item["href"]))
    return tuple(alternates)


def _default_sitemap_url(start_url: str) -> str:
    try:
        parts = urlsplit(normalize_url(start_url))
    except ValueError as exc:
        raise SitemapError(str(exc)) from exc
    return urlunsplit((parts.scheme, parts.netloc, "/sitemap.xml", "", ""))


def _update_robots(path: Path, sitemap_urls: tuple[str, ...]) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    for sitemap_url in sitemap_urls:
        text = upsert_sitemap_directive(text, sitemap_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
