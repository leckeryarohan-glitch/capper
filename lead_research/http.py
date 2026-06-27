from __future__ import annotations

import http.client
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import IO

_thread_local = threading.local()


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def thread_opener() -> urllib.request.OpenerDirector:
    opener = getattr(_thread_local, "opener", None)
    if opener is None:
        https_handler = urllib.request.HTTPSHandler(context=ssl_context())
        opener = urllib.request.build_opener(https_handler)
        _thread_local.opener = opener
    return opener


def urlopen(
    request: urllib.request.Request,
    timeout: float | None = None,
) -> http.client.HTTPResponse:
    return thread_opener().open(request, timeout=timeout)  # type: ignore[return-value]


def read_response_text(
    response: IO[bytes],
    *,
    max_bytes: int = 2_000_000,
    default_charset: str = "utf-8",
    max_seconds: float | None = None,
) -> str:
    started = time.monotonic()
    chunks: list[bytes] = []
    total = 0
    while total < max_bytes:
        if max_seconds is not None and time.monotonic() - started > max_seconds:
            raise TimeoutError("response read timed out")
        chunk = response.read(min(65_536, max_bytes - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    raw = b"".join(chunks)
    charset = default_charset
    get_charset = getattr(response, "headers", None)
    if get_charset is not None:
        charset = response.headers.get_content_charset() or default_charset
    return raw.decode(charset, errors="replace")


def format_request_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP Error {exc.code}: {exc.reason}"
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
