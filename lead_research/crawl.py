from __future__ import annotations

import threading
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from typing import Callable

from .extract import extract_emails, normalized_host, parse_page, strip_fragment
from .extract import normalize_email
from .http import read_response_text, urlopen
from .models import Lead, SearchResult, classify_email


USER_AGENT = "capper-lead-research/0.1 (+compliance-review)"

DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0
DEFAULT_ROBOTS_TIMEOUT_SECONDS = 6.0
DEFAULT_SITE_TIMEOUT_SECONDS = 40.0
DEFAULT_READ_TIMEOUT_SECONDS = 15.0

# German businesses are legally required to publish an Impressum with contact
# details, so try these common paths even when they are not linked.
CONTACT_PATH_GUESSES = (
    "/impressum",
    "/impressum/",
    "/kontakt",
    "/kontakt/",
    "/contact",
    "/contact/",
    "/imprint",
    "/legal-notice",
)


def guessed_contact_urls(
    start_url: str,
    paths: tuple[str, ...] = CONTACT_PATH_GUESSES,
) -> list[str]:
    parsed = urllib.parse.urlparse(start_url)
    if not parsed.scheme or not parsed.netloc:
        return []
    base = f"{parsed.scheme}://{parsed.netloc}"
    urls: list[str] = []
    seen: set[str] = set()
    for path in paths:
        url = base + path
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


@dataclass(frozen=True)
class CrawlConfig:
    max_pages_per_site: int = 3
    delay_seconds: float = 1.0
    include_personal: bool = False
    respect_robots: bool = True
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    robots_timeout_seconds: float = DEFAULT_ROBOTS_TIMEOUT_SECONDS
    site_timeout_seconds: float = DEFAULT_SITE_TIMEOUT_SECONDS
    read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS


class LeadCrawler:
    def __init__(
        self,
        config: CrawlConfig,
        on_page: Callable[[str], None] | None = None,
        on_lead: Callable[[Lead], None] | None = None,
    ):
        self.config = config
        self.on_page = on_page
        self.on_lead = on_lead
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._robots_lock = threading.Lock()

    def crawl_result(self, result: SearchResult, category: str) -> list[Lead]:
        if result.directory_email and not result.url.strip():
            return self._directory_email_leads(result, category)

        start_url = normalize_url(result.url)
        if not start_url:
            if result.directory_email:
                return self._directory_email_leads(result, category)
            return []

        deadline = time.monotonic() + max(self.config.site_timeout_seconds, 1.0)
        queue = [start_url]
        for guessed in guessed_contact_urls(start_url):
            if guessed not in queue:
                queue.append(guessed)
        visited: set[str] = set()
        seen_emails: set[str] = set()
        leads: list[Lead] = []
        page_title = result.title
        fetched = 0
        attempts = 0
        max_attempts = self.config.max_pages_per_site + len(CONTACT_PATH_GUESSES) + 2

        while queue and fetched < self.config.max_pages_per_site and attempts < max_attempts:
            if time.monotonic() >= deadline:
                break

            url = strip_fragment(queue.pop(0))
            if url in visited:
                continue
            if not self._allowed(url, deadline):
                visited.add(url)
                continue
            visited.add(url)
            attempts += 1
            if self.on_page:
                self.on_page(url)

            response = fetch_url(
                url,
                request_timeout=self.config.request_timeout_seconds,
                read_timeout=self.config.read_timeout_seconds,
                deadline=deadline,
            )
            if response is None:
                continue
            fetched += 1

            html_text, final_url = response
            title, contact_links = parse_page(html_text, final_url)
            page_title = title or page_title

            for email in extract_emails(html_text):
                if email in seen_emails:
                    continue
                status = classify_email(email)
                if status.value == "personal_review_required" and not self.config.include_personal:
                    continue
                seen_emails.add(email)
                lead = Lead(
                    category=category,
                    source_url=result.url,
                    website=final_url,
                    email=email,
                    company_name=infer_company_name(page_title, final_url),
                    page_title=page_title,
                    consent_status=status,
                    notes=[f"Search snippet: {result.snippet}"] if result.snippet else [],
                )
                leads.append(lead)
                if self.on_lead:
                    self.on_lead(lead)

            for offset, link in enumerate(contact_links):
                if link not in visited and link not in queue:
                    queue.insert(offset, link)

            if self.config.delay_seconds > 0 and time.monotonic() < deadline:
                remaining = min(self.config.delay_seconds, deadline - time.monotonic())
                if remaining > 0:
                    time.sleep(remaining)

        if not leads and result.directory_email:
            return self._directory_email_leads(result, category)
        return leads

    def _directory_email_leads(self, result: SearchResult, category: str) -> list[Lead]:
        email = normalize_email(result.directory_email)
        if not email:
            return []
        status = classify_email(email)
        if status.value == "personal_review_required" and not self.config.include_personal:
            return []
        source_url = result.directory_source_url or result.url or result.snippet
        website = result.url.strip()
        lead = Lead(
            category=category,
            source_url=source_url,
            website=website,
            email=email,
            company_name=result.title,
            page_title=result.title,
            consent_status=status,
            notes=[f"Branchenverzeichnis: {result.snippet}"] if result.snippet else ["Branchenverzeichnis"],
        )
        if self.on_lead:
            self.on_lead(lead)
        return [lead]

    def _allowed(self, url: str, deadline: float) -> bool:
        if not self.config.respect_robots:
            return True
        if time.monotonic() >= deadline:
            return False

        host = normalized_host(url)
        with self._robots_lock:
            parser = self._robots_cache.get(host)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            parsed = urllib.parse.urlparse(url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            parser.set_url(robots_url)
            robots_body = fetch_robots_txt(robots_url, self.config.robots_timeout_seconds, deadline)
            if robots_body is None:
                return True
            parser.parse(robots_body.splitlines())
            with self._robots_lock:
                self._robots_cache[host] = parser

        try:
            return parser.can_fetch(USER_AGENT, url)
        except Exception:
            return True


def fetch_robots_txt(robots_url: str, timeout: float, deadline: float | None = None) -> str | None:
    if deadline is not None and time.monotonic() >= deadline:
        return None
    remaining = timeout
    if deadline is not None:
        remaining = min(timeout, max(0.5, deadline - time.monotonic()))
    try:
        request = urllib.request.Request(
            robots_url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/plain"},
        )
        with urlopen(request, timeout=remaining) as response:
            return read_response_text(
                response,
                max_bytes=250_000,
                max_seconds=min(timeout, remaining),
            )
    except Exception:  # noqa: BLE001
        return None


def fetch_url(
    url: str,
    *,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    read_timeout: float = DEFAULT_READ_TIMEOUT_SECONDS,
    deadline: float | None = None,
) -> tuple[str, str] | None:
    if deadline is not None and time.monotonic() >= deadline:
        return None
    timeout = request_timeout
    if deadline is not None:
        timeout = min(request_timeout, max(0.5, deadline - time.monotonic()))
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                return None
            read_budget = read_timeout
            if deadline is not None:
                read_budget = min(read_timeout, max(0.5, deadline - time.monotonic()))
            html_text = read_response_text(response, max_bytes=1_000_000, max_seconds=read_budget)
            return html_text, response.geturl()
    except Exception:  # noqa: BLE001 - one unreachable/invalid URL must not abort a crawl
        return None


def normalize_url(url: str) -> str:
    stripped = url.strip()
    if not stripped:
        return ""
    parsed = urllib.parse.urlparse(stripped)
    if not parsed.scheme:
        stripped = f"https://{stripped}"
        parsed = urllib.parse.urlparse(stripped)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return stripped


def infer_company_name(title: str, url: str) -> str:
    if title:
        return title.split("|", 1)[0].split("-", 1)[0].strip()
    return normalized_host(url)
