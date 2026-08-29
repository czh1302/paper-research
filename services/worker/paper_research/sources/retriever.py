from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from collections import defaultdict

from ..models import CandidatePaper, QueryBundle, SearchQuery, SourceResult
from .base import LiteratureSource


def normalize_title(title: str) -> str:
    value = unicodedata.normalize("NFKD", title).casefold()
    return re.sub(r"[^a-z0-9]+", "", value)


def canonical_id(paper: CandidatePaper) -> str:
    if paper.doi:
        return f"doi:{paper.doi.casefold().removeprefix('https://doi.org/')}"
    if paper.arxiv_id:
        return f"arxiv:{paper.arxiv_id.casefold()}"
    if paper.openreview_id:
        return f"openreview:{paper.openreview_id}"
    if paper.openalex_id:
        return f"openalex:{paper.openalex_id}"
    normalized = normalize_title(paper.title)
    return f"title:{hashlib.sha256(normalized.encode()).hexdigest()[:24]}"


def merge_candidates(papers: list[CandidatePaper]) -> list[CandidatePaper]:
    merged: dict[str, CandidatePaper] = {}
    for paper in papers:
        key = canonical_id(paper)
        paper.canonical_id = key
        current = merged.get(key)
        if not current:
            merged[key] = paper
            continue
        values = current.model_dump()
        incoming = paper.model_dump()
        for field in (
            "abstract",
            "venue",
            "pdf_url",
            "doi",
            "arxiv_id",
            "openreview_id",
            "openalex_id",
        ):
            if not values.get(field) and incoming.get(field):
                values[field] = incoming[field]
        values["sources"] = sorted(set(current.sources + paper.sources))
        values["queries"] = sorted(set(current.queries + paper.queries))
        values["idea_keys"] = sorted(set(current.idea_keys + paper.idea_keys))
        values["authors"] = current.authors or paper.authors
        values["reference_ids"] = sorted(set(current.reference_ids + paper.reference_ids))
        values["citation_count"] = max(
            filter(lambda value: value is not None, [current.citation_count, paper.citation_count]),
            default=None,
        )
        values["evidence_grade"] = max(
            [current.evidence_grade, paper.evidence_grade],
            key=["metadata", "snippet", "abstract", "full_text"].index,
        )
        values["relevance_score"] = max(current.relevance_score, paper.relevance_score)
        merged[key] = CandidatePaper.model_validate(values)
    return list(merged.values())


class LiteratureRetriever:
    def __init__(self, sources: list[LiteratureSource], *, max_concurrency: int = 4) -> None:
        self.sources = sources
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self._cache: dict[tuple[str, str, int], SourceResult] = {}

    async def close(self) -> None:
        await asyncio.gather(*(source.close() for source in self.sources), return_exceptions=True)

    async def _run(self, source: LiteratureSource, query: SearchQuery, limit: int) -> SourceResult:
        cache_key = (source.name, query.query.casefold().strip(), limit)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached.model_copy(deep=True)
        try:
            async with self.semaphore:
                result = await source.search(query, limit=limit)
        except Exception as error:
            return SourceResult(
                source=source.name,
                query=query.query,
                papers=[],
                warning=f"{type(error).__name__}: {error}",
            )
        if len(self._cache) >= 2_000:
            self._cache.pop(next(iter(self._cache)))
        self._cache[cache_key] = result.model_copy(deep=True)
        return result

    async def retrieve(
        self, bundle: QueryBundle, *, per_source_limit: int = 10
    ) -> tuple[list[CandidatePaper], list[dict[str, object]]]:
        academic_sources = {
            "arxiv", "openreview", "openalex", "crossref", "dblp", "serper_scholar"
        }

        def routed(source: LiteratureSource, query: SearchQuery) -> bool:
            if query.source_hint == "academic":
                return source.name in academic_sources
            if query.source_hint == "web":
                return source.name not in academic_sources
            return True

        tasks = [
            self._run(source, query, per_source_limit)
            for query in bundle.queries
            for source in self.sources
            if routed(source, query)
        ]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        papers: list[CandidatePaper] = []
        audit: list[dict[str, object]] = []
        for result in raw_results:
            if isinstance(result, Exception):
                audit.append({"source": "unknown", "error": str(result), "count": 0})
                continue
            papers.extend(result.papers)
            audit.append(
                {
                    "source": result.source,
                    "query": result.query,
                    "count": len(result.papers),
                    "warning": result.warning,
                }
            )
        return merge_candidates(papers), audit


def source_coverage(papers: list[CandidatePaper]) -> dict[str, int]:
    counts: defaultdict[str, int] = defaultdict(int)
    for paper in papers:
        for source in paper.sources:
            counts[source] += 1
    return dict(sorted(counts.items()))
