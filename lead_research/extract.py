from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse


EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+(?:\s*(?:@|\[at\]|\(at\)| at )\s*)[A-Z0-9.-]+"
    r"(?:\s*(?:\.|\[dot\]|\(dot\)| dot )\s*)[A-Z]{2,}\b",
    re.IGNORECASE,
)
PHONE_RE = re.compile(r"(?:\+|00)[0-9][0-9\s()./-]{6,}[0-9]")

CONTACT_HINTS = (
    "contact",
    "kontakt",
    "impressum",
    "about",
    "ueber",
    "über",
    "team",
    "reservierung",
    "booking",
)

BLOCKED_EMAIL_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".css",
    ".js",
)


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.links: list[tuple[str, str]] = []
        self._in_title = False
        self._title_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "a":
            href = attr_map.get("href", "")
            label = attr_map.get("title", "") or attr_map.get("aria-label", "")
            self.links.append((href, label))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
            self.title = normalize_whitespace(" ".join(self._title_chunks))

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_chunks.append(data)


def extract_emails(text: str) -> list[str]:
    found: set[str] = set()
    normalized_text = html.unescape(text)
    for match in EMAIL_RE.findall(normalized_text):
        email = normalize_email(match)
        if not email or email.lower().endswith(BLOCKED_EMAIL_SUFFIXES):
            continue
        found.add(email.lower())
    return sorted(found)


def extract_phone(text: str) -> str:
    match = PHONE_RE.search(text)
    if not match:
        return ""
    return normalize_whitespace(match.group(0))


def parse_page(html_text: str, base_url: str) -> tuple[str, list[str]]:
    parser = PageParser()
    parser.feed(html_text)

    links: list[str] = []
    for href, label in parser.links:
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        if is_same_site(base_url, absolute) and looks_like_contact_url(absolute, label):
            links.append(strip_fragment(absolute))
    return parser.title, sorted(set(links))


def looks_like_contact_url(url: str, label: str = "") -> bool:
    candidate = f"{url} {label}".lower()
    return any(hint in candidate for hint in CONTACT_HINTS)


def is_same_site(base_url: str, candidate_url: str) -> bool:
    base_host = normalized_host(base_url)
    candidate_host = normalized_host(candidate_url)
    return candidate_host == base_host or candidate_host.endswith(f".{base_host}")


def normalized_host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def strip_fragment(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


def normalize_email(value: str) -> str:
    cleaned = html.unescape(value).strip().lower()
    replacements = {
        " at ": "@",
        "[at]": "@",
        "(at)": "@",
        " dot ": ".",
        "[dot]": ".",
        "(dot)": ".",
    }
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned.strip(".,;:()[]{}<>\"'")


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
