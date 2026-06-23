from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from typing import Callable

from .extract import extract_emails, extract_phone, normalized_host, parse_page, strip_fragment
from .models import Lead, SearchResult, classify_email


USER_AGENT = "capper-lead-research/0.1 (+compliance-review)"


@dataclass(frozen=True)
class CrawlConfig:
    max_pages_per_site: int = 3
    delay_seconds: float = 1.0
    include_personal: bool = False
    respect_robots: bool = True


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

    def crawl_result(self, result: SearchResult, category: str) -> list[Lead]:
        start_url = normalize_url(result.url)
        if not start_url:
            return []

        queue = [start_url]
        visited: set[str] = set()
        leads: list[Lead] = []
        page_title = result.title
        phone = ""

        while queue and len(visited) < self.config.max_pages_per_site:
            url = strip_fragment(queue.pop(0))
            if url in visited or not self._allowed(url):
                continue
            visited.add(url)
            if self.on_page:
                self.on_page(url)

            response = fetch_url(url)
            if response is None:
                continue

            html_text, final_url = response
            title, contact_links = parse_page(html_text, final_url)
            page_title = title or page_title
            phone = phone or extract_phone(html_text)

            for email in extract_emails(html_text):
                status = classify_email(email)
                if status.value == "personal_review_required" and not self.config.include_personal:
                    continue
                lead = Lead(
                    category=category,
                    source_url=result.url,
                    website=final_url,
                    email=email,
                    company_name=infer_company_name(page_title, final_url),
                    phone=phone,
                    page_title=page_title,
                    consent_status=status,
                    notes=[f"Search snippet: {result.snippet}"] if result.snippet else [],
                )
                leads.append(lead)
                if self.on_lead:
                    self.on_lead(lead)

            for link in contact_links:
                if link not in visited and link not in queue:
                    queue.append(link)

            if self.config.delay_seconds > 0:
                time.sleep(self.config.delay_seconds)

        return leads

    def _allowed(self, url: str) -> bool:
        if not self.config.respect_robots:
            return True

        host = normalized_host(url)
        parser = self._robots_cache.get(host)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            parsed = urllib.parse.urlparse(url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            parser.set_url(robots_url)
            try:
                parser.read()
            except Exception:
                return True
            self._robots_cache[host] = parser

        try:
            return parser.can_fetch(USER_AGENT, url)
        except Exception:
            return True


def fetch_url(url: str) -> tuple[str, str] | None:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                return None
            raw = response.read(1_000_000)
            charset = response.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace"), response.geturl()
    except (urllib.error.URLError, TimeoutError, ValueError):
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
