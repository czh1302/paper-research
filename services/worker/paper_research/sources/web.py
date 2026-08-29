from __future__ import annotations

from typing import Any

from ..models import CandidatePaper, SearchQuery, SourceResult
from .base import LiteratureSource


class SerperSource(LiteratureSource):
    name = "serper"

    def __init__(self, api_key: str, *, scholar: bool = True) -> None:
        super().__init__(requests_per_second=2)
        self.api_key = api_key
        self.scholar = scholar
        self.name = "serper_scholar" if scholar else "serper_web"

    async def search(self, query: SearchQuery, limit: int = 10) -> SourceResult:
        await self.rate_limiter.wait()
        endpoint = "scholar" if self.scholar else "search"
        response = await self.client.post(
            f"https://google.serper.dev/{endpoint}",
            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
            json={"q": query.query, "num": limit},
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("organic") or []
        papers = []
        for item in items[:limit]:
            link = item.get("link")
            title = item.get("title")
            if not link or not title:
                continue
            papers.append(
                CandidatePaper(
                    title=title,
                    abstract=item.get("snippet") or "",
                    year=_publication_year(item),
                    authors=_publication_authors(item),
                    url=link,
                    citation_count=_citation_count(item),
                    sources=[self.name],
                    queries=[query.query],
                    evidence_grade="snippet",
                )
            )
        return SourceResult(source=self.name, query=query.query, papers=papers)


def _publication_year(item: dict[str, Any]) -> int | None:
    import re

    match = re.search(r"(?:19|20)\d{2}", str(item.get("publicationInfo") or item.get("date") or ""))
    return int(match.group()) if match else None


def _publication_authors(item: dict[str, Any]) -> list[str]:
    raw = item.get("publicationInfo")
    if isinstance(raw, dict):
        summary = raw.get("summary") or ""
        return [part.strip() for part in summary.split("-")[0].split(",") if part.strip()]
    return []


def _citation_count(item: dict[str, Any]) -> int | None:
    import re

    match = re.search(r"\d+", str(item.get("citedBy") or ""))
    return int(match.group()) if match else None


class TavilySource(LiteratureSource):
    name = "tavily"

    def __init__(self, api_key: str) -> None:
        super().__init__(requests_per_second=1)
        self.api_key = api_key

    async def search(self, query: SearchQuery, limit: int = 10) -> SourceResult:
        await self.rate_limiter.wait()
        response = await self.client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": self.api_key,
                "query": query.query,
                "search_depth": "advanced",
                "max_results": limit,
                "include_answer": False,
                "include_raw_content": False,
            },
        )
        response.raise_for_status()
        papers = [
            CandidatePaper(
                title=item.get("title") or "Untitled",
                abstract=item.get("content") or "",
                url=item["url"],
                sources=[self.name],
                queries=[query.query],
                relevance_score=max(0, min(float(item.get("score", 0)), 1)),
                evidence_grade="snippet",
            )
            for item in response.json().get("results", [])
            if item.get("url")
        ]
        return SourceResult(source=self.name, query=query.query, papers=papers)
