from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path

from .models import SearchResult


class SearchProviderError(RuntimeError):
    pass


class SearchProvider(ABC):
    @abstractmethod
    def search(self, category: str, location: str, limit: int) -> list[SearchResult]:
        raise NotImplementedError


class FileSearchProvider(SearchProvider):
    """Reads seed URLs from a plain text file, one URL per line."""

    def __init__(self, path: Path):
        self.path = path

    def search(self, category: str, location: str, limit: int) -> list[SearchResult]:
        if not self.path.exists():
            raise SearchProviderError(f"Seed file does not exist: {self.path}")

        results: list[SearchResult] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            results.append(SearchResult(title=stripped, url=stripped))
            if len(results) >= limit:
                break
        return results


class BraveSearchProvider(SearchProvider):
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("BRAVE_SEARCH_API_KEY")
        if not self.api_key:
            raise SearchProviderError("BRAVE_SEARCH_API_KEY is required for Brave search.")

    def search(self, category: str, location: str, limit: int) -> list[SearchResult]:
        query = build_query(category, location)
        params = urllib.parse.urlencode({"q": query, "count": min(limit, 20)})
        request = urllib.request.Request(
            f"{self.endpoint}?{params}",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
                "User-Agent": "capper-lead-research/0.1",
            },
        )
        data = _read_json(request)
        web_results = data.get("web", {}).get("results", [])
        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("description", ""),
            )
            for item in web_results
            if item.get("url")
        ][:limit]


class BingSearchProvider(SearchProvider):
    endpoint = "https://api.bing.microsoft.com/v7.0/search"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("BING_SEARCH_API_KEY")
        if not self.api_key:
            raise SearchProviderError("BING_SEARCH_API_KEY is required for Bing search.")

    def search(self, category: str, location: str, limit: int) -> list[SearchResult]:
        query = build_query(category, location)
        params = urllib.parse.urlencode({"q": query, "count": min(limit, 50)})
        request = urllib.request.Request(
            f"{self.endpoint}?{params}",
            headers={
                "Accept": "application/json",
                "Ocp-Apim-Subscription-Key": self.api_key,
                "User-Agent": "capper-lead-research/0.1",
            },
        )
        data = _read_json(request)
        web_results = data.get("webPages", {}).get("value", [])
        return [
            SearchResult(
                title=item.get("name", ""),
                url=item.get("url", ""),
                snippet=item.get("snippet", ""),
            )
            for item in web_results
            if item.get("url")
        ][:limit]


class SerpApiSearchProvider(SearchProvider):
    endpoint = "https://serpapi.com/search.json"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("SERPAPI_API_KEY")
        if not self.api_key:
            raise SearchProviderError("SERPAPI_API_KEY is required for SerpAPI search.")

    def search(self, category: str, location: str, limit: int) -> list[SearchResult]:
        query = build_query(category, location)
        params = urllib.parse.urlencode({"engine": "google", "q": query, "api_key": self.api_key})
        request = urllib.request.Request(
            f"{self.endpoint}?{params}",
            headers={"Accept": "application/json", "User-Agent": "capper-lead-research/0.1"},
        )
        data = _read_json(request)
        organic_results = data.get("organic_results", [])
        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
            )
            for item in organic_results
            if item.get("link")
        ][:limit]


def build_query(category: str, location: str) -> str:
    parts = [category, "Unternehmen", "Kontakt", "E-Mail"]
    if location:
        parts.insert(1, location)
    return " ".join(part for part in parts if part).strip()


def provider_from_name(name: str, seed_file: Path | None = None) -> SearchProvider:
    normalized = name.lower()
    if normalized == "file":
        if seed_file is None:
            raise SearchProviderError("--seed-file is required when --provider=file")
        return FileSearchProvider(seed_file)
    if normalized == "brave":
        return BraveSearchProvider()
    if normalized == "bing":
        return BingSearchProvider()
    if normalized == "serpapi":
        return SerpApiSearchProvider()
    raise SearchProviderError(f"Unsupported provider: {name}")


def _read_json(request: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except OSError as exc:
        raise SearchProviderError(f"Search provider request failed: {exc}") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SearchProviderError("Search provider returned invalid JSON.") from exc
