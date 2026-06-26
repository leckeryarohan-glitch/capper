from __future__ import annotations

import http.client
import ssl
import urllib.error
import urllib.request
from typing import IO


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def urlopen(
    request: urllib.request.Request,
    timeout: float | None = None,
) -> http.client.HTTPResponse:
    return urllib.request.urlopen(request, timeout=timeout, context=ssl_context())


def read_response_text(
    response: IO[bytes],
    *,
    max_bytes: int = 2_000_000,
    default_charset: str = "utf-8",
) -> str:
    raw = response.read(max_bytes)
    charset = default_charset
    get_charset = getattr(response, "headers", None)
    if get_charset is not None:
        charset = response.headers.get_content_charset() or default_charset
    return raw.decode(charset, errors="replace")


def format_request_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        detail = _read_http_error_body(exc)
        if exc.code in {401, 403}:
            return (
                f"HTTP Error {exc.code}: ZenRows API-Key ungueltig oder kein Zugriff"
                + (f" ({detail})" if detail else "")
            )
        suffix = f" ({detail})" if detail else ""
        return f"HTTP Error {exc.code}: {exc.reason}{suffix}"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            return (
                f"{reason}. "
                "SSL-Zertifikatsfehler: fuehre 'pip install certifi' aus oder auf macOS "
                "'/Applications/Python 3.x/Install Certificates.command'."
            )
        return str(reason)
    return str(exc)


def is_auth_http_error(exc: BaseException) -> bool:
    return isinstance(exc, urllib.error.HTTPError) and exc.code in {401, 403}


def _read_http_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace").strip()[:300]
    except Exception:  # noqa: BLE001
        return ""
