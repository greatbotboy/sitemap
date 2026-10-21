"""CLI build and crawl commands."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from sitemapgen.cli import main


def test_build_from_urls_and_robots(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    urls = tmp_path / "urls.txt"
    urls.write_text("http://example.com/\n\nhttp://example.com/catalog?item=1&x=2\n", encoding="utf-8")
    robots = tmp_path / "robots.txt"
    robots.write_text("User-agent: *\nDisallow: /tmp\n", encoding="utf-8")
    output = tmp_path / "out"
    code = main(
        [
            "build",
            "--sitemap-url",
            "http://example.com/sitemap.xml",
            "--urls",
            str(urls),
            "--output",
            str(output),
            "--robots",
            str(robots),
        ]
    )
    assert code == 0
    body = (output / "sitemap.xml").read_text(encoding="utf-8")
    assert "item=1&amp;x=2" in body
    text = robots.read_text(encoding="utf-8")
    assert text == (
        "User-agent: *\n"
        "Disallow: /tmp\n"
        "Sitemap: http://example.com/sitemap.xml\n"
    )
    captured = capsys.readouterr()
    assert "sitemap.xml" in captured.out
    assert captured.err == ""

    again = main(
        [
            "build",
            "--sitemap-url",
            "http://example.com/sitemap.xml",
            "--urls",
            str(urls),
            "--output",
            str(output),
            "--robots",
            str(robots),
        ]
    )
    assert again == 0
    assert robots.read_text(encoding="utf-8") == text


def test_build_jsonl_and_dropped_urls(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "entries.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "loc": "http://example.com/a",
                        "lastmod": "2005-01-01",
                        "changefreq": "weekly",
                        "priority": 0.3,
                    }
                ),
                json.dumps({"loc": "https://example.com/nope"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "out"
    code = main(
        [
            "build",
            "--sitemap-url",
            "http://example.com/sitemap.xml",
            "--jsonl",
            str(source),
            "--output",
            str(output),
            "--gzip",
        ]
    )
    assert code == 0
    assert (output / "sitemap.xml.gz").is_file()
    captured = capsys.readouterr()
    assert "dropped https://example.com/nope: URL scheme does not match the sitemap" in captured.err


def test_build_with_no_valid_urls_exits_nonzero(tmp_path: Path) -> None:
    urls = tmp_path / "urls.txt"
    urls.write_text("ftp://example.com/\n", encoding="utf-8")
    code = main(
        [
            "build",
            "--sitemap-url",
            "http://example.com/sitemap.xml",
            "--urls",
            str(urls),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    assert code == 1
    assert not (tmp_path / "out").exists()


def test_invalid_jsonl_is_an_input_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "entries.jsonl"
    source.write_text("{not json}\n", encoding="utf-8")
    code = main(
        [
            "build",
            "--sitemap-url",
            "http://example.com/sitemap.xml",
            "--jsonl",
            str(source),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    assert code == 1
    assert "invalid JSON" in capsys.readouterr().err


def test_missing_arguments_exit_with_argparse_status() -> None:
    with pytest.raises(SystemExit) as caught:
        main(["build"])
    assert caught.value.code == 2


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b'<html><a href="/about">About</a></html>'
        if self.path.split("?", 1)[0] == "/about":
            body = b"<html>about</html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


def test_crawl_command_writes_a_sitemap(tmp_path: Path) -> None:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    output = tmp_path / "out"
    try:
        code = main(
            [
                "crawl",
                f"http://127.0.0.1:{port}/",
                "--output",
                str(output),
                "--max-pages",
                "10",
            ]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert code == 0
    body = (output / "sitemap.xml").read_text(encoding="utf-8")
    assert f"http://127.0.0.1:{port}/" in body
    assert f"http://127.0.0.1:{port}/about" in body
