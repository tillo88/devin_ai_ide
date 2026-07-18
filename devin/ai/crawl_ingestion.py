"""Optional Crawl4AI-powered knowledge ingestion.

Crawl4AI is treated as an optional adapter. If it is not installed, callers can
fall back to DEVIN's basic URL fetcher. Network safety checks live at the API
boundary (`_validate_public_url`) so this module stays reusable in tests.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Optional

from devin.ai.structured_contracts import CrawlKnowledgeRecord
from devin.ai.web_search import fetch_page_text


@dataclass
class CrawlAdapterStatus:
    available: bool
    backend: str
    error: str = ""


def crawl4ai_status() -> CrawlAdapterStatus:
    try:
        import crawl4ai  # noqa: F401
        return CrawlAdapterStatus(available=True, backend="crawl4ai")
    except Exception as exc:
        return CrawlAdapterStatus(available=False, backend="basic_fetch", error=str(exc))


def _markdown_from_crawl4ai_result(result) -> str:
    markdown = getattr(result, "markdown", "")
    if isinstance(markdown, str):
        return markdown
    for attr in ("fit_markdown", "raw_markdown", "markdown"):
        value = getattr(markdown, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return str(markdown or "")


async def _crawl_with_crawl4ai(url: str, max_chars: int) -> CrawlKnowledgeRecord:
    from crawl4ai import AsyncWebCrawler

    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=url)
    markdown = _markdown_from_crawl4ai_result(result)[:max_chars]
    links = []
    raw_links = getattr(result, "links", None)
    if isinstance(raw_links, dict):
        for values in raw_links.values():
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, dict) and item.get("href"):
                        links.append(str(item["href"]))
                    elif isinstance(item, str):
                        links.append(item)
    metadata = getattr(result, "metadata", None) or {}
    title = ""
    if isinstance(metadata, dict):
        title = str(metadata.get("title") or "")
    return CrawlKnowledgeRecord(
        url=url,
        title=title,
        markdown=markdown,
        source="crawl4ai",
        links=links[:100],
        metadata=metadata if isinstance(metadata, dict) else {},
    )


async def crawl_url_to_knowledge(
    url: str,
    mode: str = "auto",
    max_chars: int = 50000,
    basic_fetcher: Optional[Callable[[str, int, int], str]] = None,
) -> CrawlKnowledgeRecord:
    """Return normalized crawl output for project knowledge.

    mode:
    - auto: try Crawl4AI, fallback to basic fetch if unavailable/failing.
    - crawl4ai: require Crawl4AI; raise if unavailable/failing.
    - basic: use existing lightweight fetch_page_text.
    """
    mode = (mode or "auto").lower()
    safe_max_chars = max(1000, min(int(max_chars or 50000), 200000))
    fetcher = basic_fetcher or fetch_page_text

    if mode not in {"auto", "crawl4ai", "basic"}:
        raise ValueError("mode must be auto, crawl4ai, or basic")

    if mode in {"auto", "crawl4ai"}:
        try:
            return await _crawl_with_crawl4ai(url, safe_max_chars)
        except Exception:
            if mode == "crawl4ai":
                raise

    text = await asyncio.to_thread(fetcher, url, safe_max_chars, 15)
    return CrawlKnowledgeRecord(
        url=url,
        title=url,
        markdown=(text or "")[:safe_max_chars],
        source="basic_fetch",
        links=[],
        metadata={"fallback": mode == "auto"},
    )
