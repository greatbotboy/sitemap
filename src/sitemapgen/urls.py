"""URL normalization, sitemap location rules, and entry metadata."""

from __future__ import annotations

import calendar
import ipaddress
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import SplitResult, quote, unquote, urlsplit, urlunsplit

from sitemapgen.models import (
    LOC_MAX,
    AlternateLink,
    ChangeFreq,
    ImageInfo,
    PreparedAlternate,
    PreparedImage,
    PreparedUrl,
    UrlEntry,
)

_CHANGEFREQ = tuple(item.value for item in ChangeFreq)
_CHANGEFREQ_MESSAGE = "changefreq must be one of: " + ", ".join(_CHANGEFREQ)

# W3C datetime profile: https://www.w3.org/TR/NOTE-datetime
_W3C = re.compile(
    r"^(?P<year>\d{4})"
    r"(?:-(?P<month>\d{2})"
    r"(?:-(?P<day>\d{2})"
    r"(?:T(?P<hour>\d{2}):(?P<minute>\d{2})"
    r"(?::(?P<second>\d{2})(?:\.(?P<fraction>\d+))?)?"
    r"(?P<tz>Z|[+-]\d{2}:\d{2}))?)?)?$"
)


def normalize_url(url: str) -> str:
    """Return a UTF-8 percent-encoded absolute http(s) URL without a fragment."""

    if not isinstance(url, str):
        raise ValueError("URL must be a string")
    raw = url.strip()
    if not raw:
        raise ValueError("URL is empty")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ValueError("URL contains a control character")

    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError("URL scheme must be http or https")
    try:
        _ = parts.port
    except ValueError as exc:
        raise ValueError("URL port is not valid") from exc

    netloc = _format_netloc(parts)
    path = _encode_path(parts.path)
    query = _encode_query(parts.query)
    normalized = urlunsplit((scheme, netloc, path, query, ""))
    if len(normalized) >= LOC_MAX:
        raise ValueError("URL must be less than 2048 characters")
    return normalized


def location_problem(page_url: str, sitemap_url: str) -> str | None:
    """Return why ``page_url`` cannot appear in a sitemap hosted at ``sitemap_url``.

    Both arguments must already be normalized. A page is valid when it uses the
    sitemap's scheme, host, and port, and its path is under that file's directory.
    An explicit port on the sitemap URL must be repeated on every page URL.
    """

    page = urlsplit(page_url)
    sitemap = urlsplit(sitemap_url)
    if page.scheme != sitemap.scheme:
        return "URL scheme does not match the sitemap"
    if (page.hostname or "") != (sitemap.hostname or ""):
        return "URL host does not match the sitemap"
    sitemap_port = _explicit_port(sitemap)
    if sitemap_port is not None and _explicit_port(page) != sitemap_port:
        return f"URL must include port {sitemap_port}"
    if _effective_port(page) != _effective_port(sitemap):
        return "URL port does not match the sitemap"
    prefix = directory_path(sitemap.path)
    if not page.path.startswith(prefix):
        return "URL is outside the sitemap directory"
    return None


def directory_path(path: str) -> str:
    """Directory of a sitemap path, including the trailing slash."""

    if not path or path == "/":
        return "/"
    if path.endswith("/"):
        return path
    parent, _separator, _name = path.rpartition("/")
    if not parent:
        return "/"
    return parent + "/"


def same_origin(left: str, right: str) -> bool:
    left_parts = urlsplit(left)
    right_parts = urlsplit(right)
    return (
        left_parts.scheme == right_parts.scheme
        and (left_parts.hostname or "").lower() == (right_parts.hostname or "").lower()
        and _effective_port(left_parts) == _effective_port(right_parts)
    )


def url_key(url: str) -> tuple[str, str, int, str, str]:
    """Identity used to collapse default-port and host-case duplicates."""

    parts = urlsplit(url)
    return (
        parts.scheme,
        (parts.hostname or "").lower(),
        _effective_port(parts),
        parts.path,
        parts.query,
    )


def prepare_entry(entry: UrlEntry, sitemap_url: str) -> tuple[PreparedUrl | None, str | None]:
    try:
        if not isinstance(entry.loc, str):
            raise ValueError("URL must be a string")
        loc = normalize_url(entry.loc)
        problem = location_problem(loc, sitemap_url)
        if problem:
            return None, problem
        lastmod = None if entry.lastmod is None else format_lastmod(entry.lastmod)
        changefreq = None if entry.changefreq is None else format_changefreq(entry.changefreq)
        priority = None if entry.priority is None else format_priority(entry.priority)
        images = _prepare_images(entry.images)
        alternates = _prepare_alternates(entry.alternates)
    except ValueError as exc:
        return None, str(exc)
    return PreparedUrl(loc, lastmod, changefreq, priority, images, alternates), None


def _prepare_images(images: object) -> tuple[PreparedImage, ...]:
    if images is None:
        return ()
    if isinstance(images, (str, bytes)) or not isinstance(images, (tuple, list)):
        raise ValueError("images must be a sequence of image entries")
    prepared: list[PreparedImage] = []
    seen: set[str] = set()
    for image in images:
        if len(prepared) >= 1_000:
            break
        if not isinstance(image, ImageInfo):
            raise ValueError("images must be a sequence of image entries")
        try:
            loc = normalize_url(image.loc)
        except ValueError:
            continue
        if loc in seen:
            continue
        seen.add(loc)
        prepared.append(PreparedImage(loc, _clean_text(image.title)))
    return tuple(prepared)


def _prepare_alternates(alternates: object) -> tuple[PreparedAlternate, ...]:
    if alternates is None:
        return ()
    if isinstance(alternates, (str, bytes)) or not isinstance(alternates, (tuple, list)):
        raise ValueError("alternates must be a sequence of hreflang links")
    prepared: list[PreparedAlternate] = []
    seen: set[tuple[str, str]] = set()
    for alternate in alternates:
        if len(prepared) >= 100:
            break
        if not isinstance(alternate, AlternateLink):
            raise ValueError("alternates must be a sequence of hreflang links")
        hreflang = _clean_hreflang(alternate.hreflang)
        if hreflang is None:
            continue
        try:
            href = normalize_url(alternate.href)
        except ValueError:
            continue
        key = (hreflang.lower(), href)
        if key in seen:
            continue
        seen.add(key)
        prepared.append(PreparedAlternate(hreflang, href))
    return tuple(prepared)


_HREFLANG = re.compile(r"^(?:[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*|x-default)$", re.IGNORECASE)


def _clean_hreflang(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.lower() == "x-default":
        return "x-default"
    if _HREFLANG.fullmatch(text) is None:
        return None
    return text


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text[:1_000]


def format_changefreq(value: object) -> str:
    if isinstance(value, ChangeFreq):
        return value.value
    if not isinstance(value, str) or value not in _CHANGEFREQ:
        raise ValueError(_CHANGEFREQ_MESSAGE)
    return value


def format_priority(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("priority must be a number from 0.0 to 1.0")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("priority must be a number from 0.0 to 1.0") from exc
    if not number.is_finite() or number < 0 or number > 1:
        raise ValueError("priority must be a number from 0.0 to 1.0")
    text = format(number, "f")
    if "." not in text:
        return text + ".0"
    whole, fraction = text.split(".", 1)
    fraction = fraction.rstrip("0")
    return whole + "." + (fraction or "0")


def format_lastmod(value: object) -> str:
    if isinstance(value, datetime):
        return _format_datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        _validate_w3c(value)
        return value
    raise ValueError("lastmod must be a W3C datetime")


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    offset = value.utcoffset()
    if offset is None:
        raise ValueError("lastmod must be a W3C datetime")
    tz = _format_offset(offset)
    text = value.strftime("%Y-%m-%dT%H:%M:%S")
    if value.microsecond:
        fraction = f"{value.microsecond:06d}".rstrip("0")
        text = f"{text}.{fraction}"
    return text + tz


def _format_offset(offset: timedelta) -> str:
    seconds = int(offset.total_seconds())
    sign = "+" if seconds >= 0 else "-"
    seconds = abs(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours > 23 or remainder % 60 != 0:
        raise ValueError("lastmod must be a W3C datetime")
    return f"{sign}{hours:02d}:{minutes:02d}"


def _validate_w3c(value: str) -> None:
    match = _W3C.fullmatch(value)
    if match is None:
        raise ValueError("lastmod must be a W3C datetime")
    year = int(match.group("year"))
    month = match.group("month")
    day = match.group("day")
    if month is not None:
        month_number = int(month)
        if not 1 <= month_number <= 12:
            raise ValueError("lastmod must be a W3C datetime")
        if day is not None:
            day_number = int(day)
            last_day = calendar.monthrange(year, month_number)[1]
            if not 1 <= day_number <= last_day:
                raise ValueError("lastmod must be a W3C datetime")
    hour = match.group("hour")
    if hour is None:
        return
    if not 0 <= int(hour) <= 23:
        raise ValueError("lastmod must be a W3C datetime")
    if not 0 <= int(match.group("minute")) <= 59:
        raise ValueError("lastmod must be a W3C datetime")
    second = match.group("second")
    if second is not None and not 0 <= int(second) <= 59:
        raise ValueError("lastmod must be a W3C datetime")
    tz = match.group("tz")
    if tz is None:
        raise ValueError("lastmod must be a W3C datetime")
    if tz == "Z":
        return
    tz_hour = int(tz[1:3])
    tz_minute = int(tz[4:6])
    if not 0 <= tz_hour <= 23 or not 0 <= tz_minute <= 59:
        raise ValueError("lastmod must be a W3C datetime")


def _format_netloc(parts: SplitResult) -> str:
    if not parts.hostname:
        raise ValueError("URL is missing a host")
    host = _format_host(parts.hostname)
    auth = ""
    if parts.username is not None:
        auth = quote(unquote(parts.username), safe="")
        if parts.password is not None:
            auth += ":" + quote(unquote(parts.password), safe="")
        auth += "@"
    port = _explicit_port(parts)
    port_text = f":{port}" if port else ""
    return auth + host + port_text


def _format_host(hostname: str) -> str:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            return hostname.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("URL host is not valid") from exc
    if isinstance(address, ipaddress.IPv6Address):
        return f"[{address.compressed}]"
    return str(address)


def _encode_path(path: str) -> str:
    if path in ("", "/"):
        return "/"
    # Split before quoting so slashes stay separators. Joining "/" by itself
    # would collapse to an empty string, which is handled above.
    return "/".join(quote(unquote(segment), safe="-._~") for segment in path.split("/"))


def _encode_query(query: str) -> str:
    if query == "":
        return ""
    pieces: list[str] = []
    for part in query.split("&"):
        if "=" in part:
            key, value = part.split("=", 1)
            pieces.append(
                quote(unquote(key), safe="-._~")
                + "="
                + quote(unquote(value), safe="-._~+")
            )
        else:
            pieces.append(quote(unquote(part), safe="-._~+"))
    return "&".join(pieces)


def _explicit_port(parts: SplitResult) -> str | None:
    hostport = parts.netloc.rsplit("@", 1)[-1]
    if hostport.startswith("["):
        end = hostport.find("]")
        rest = hostport[end + 1 :]
        if rest.startswith(":") and rest[1:].isdigit():
            return rest[1:]
        return None
    if ":" in hostport:
        port = hostport.rsplit(":", 1)[1]
        if port.isdigit():
            return port
    return None


def _effective_port(parts: SplitResult) -> int:
    if parts.port is not None:
        return parts.port
    return 443 if parts.scheme == "https" else 80
