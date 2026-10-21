"""Crawler against a local HTTP server."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

from sitemapgen import crawl


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/robots.txt":
            body = b"User-agent: *\nDisallow: /secret\n"
            content_type = "text/plain; charset=utf-8"
        elif path == "/":
            body = (
                b"<html><body>"
                b'<a href="/old">Old</a>'
                b'<a href="http://example.com/outside">Out</a>'
                b'<a href="/secret">Secret</a>'
                b'<a href="/notes.txt">Notes</a>'
                b"</body></html>"
            )
            content_type = "text/html; charset=utf-8"
        elif path == "/about":
            body = b"<html><body>About</body></html>"
            content_type = "text/html; charset=utf-8"
        elif path == "/secret":
            body = b"<html><body>Secret</body></html>"
            content_type = "text/html; charset=utf-8"
        elif path == "/notes.txt":
            body = b"notes"
            content_type = "text/plain; charset=utf-8"
        elif path == "/old":
            self.send_response(302)
            self.send_header("Location", "/about")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        else:
            self.send_error(404)
            return
        data = body
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:
        return


def _serve() -> tuple[HTTPServer, threading.Thread, str]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, thread, f"http://127.0.0.1:{port}/"


def test_crawl_follows_same_host_links_and_skips_the_rest() -> None:
    server, thread, start = _serve()
    try:
        entries = crawl(start, max_pages=20)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    locs = [entry.loc for entry in entries]
    assert locs == [start, start + "about"]
    assert all("example.com" not in loc for loc in locs)
    assert all(not loc.endswith("/secret") for loc in locs)
    assert all(not loc.endswith("/notes.txt") for loc in locs)
    assert all(not loc.endswith("/old") for loc in locs)


def test_ignore_robots_records_the_disallowed_page() -> None:
    server, thread, start = _serve()
    try:
        entries = crawl(start, respect_robots=False, max_pages=20)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    locs = [entry.loc for entry in entries]
    assert start + "secret" in locs
    assert start + "notes.txt" not in locs


def test_max_pages_stops_after_the_first_html_page() -> None:
    server, thread, start = _serve()
    try:
        entries = crawl(start, max_pages=1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert [entry.loc for entry in entries] == [start]
