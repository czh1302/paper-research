from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote_plus

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ..config import Settings
from ..models import CandidatePaper, SearchQuery, SourceResult
from .base import LiteratureSource


def _retryable_http_error(error: BaseException) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code == 429 or error.response.status_code >= 500
    return isinstance(error, httpx.TransportError)


def _year(value: Any) -> int | None:
    match = re.search(r"(?:19|20)\d{2}", str(value or ""))
    return int(match.group()) if match else None


def _authors(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    output = []
    for value in values:
        if isinstance(value, str):
            output.append(value)
        elif isinstance(value, dict):
            output.append(value.get("name") or value.get("display_name") or "")
    return [value for value in output if value]


class JsonSource(LiteratureSource):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=12),
        retry=retry_if_exception(_retryable_http_error),
        reraise=True,
    )
    async def get_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        await self.rate_limiter.wait()
        response = await self.client.get(url, **kwargs)
        response.raise_for_status()
        return response.json()


class ArxivSource(LiteratureSource):
    name = "arxiv"
    namespace = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

    def __init__(self) -> None:
        super().__init__(requests_per_second=1 / 3)

    async def search(self, query: SearchQuery, limit: int = 10) -> SourceResult:
        await self.rate_limiter.wait()
        url = (
            "https://export.arxiv.org/api/query?search_query=all:"
            f"{quote_plus(query.query)}&start=0&max_results={limit}&sortBy=relevance"
        )
        response = await self.client.get(url, headers={"User-Agent": "PaperResearch/0.1"})
        response.raise_for_status()
        root = ET.fromstring(response.text)
        papers = []
        for entry in root.findall("atom:entry", self.namespace):
            entry_url = entry.findtext("atom:id", default="", namespaces=self.namespace)
            arxiv_id = entry_url.rsplit("/", 1)[-1].split("v", 1)[0]
            links = {
                link.attrib.get("type"): link.attrib.get("href")
                for link in entry.findall("atom:link", self.namespace)
            }
            papers.append(
                CandidatePaper(
                    title=" ".join(
                        entry.findtext("atom:title", default="", namespaces=self.namespace).split()
                    ),
                    abstract=" ".join(
                        entry.findtext(
                            "atom:summary", default="", namespaces=self.namespace
                        ).split()
                    ),
                    year=_year(
                        entry.findtext("atom:published", default="", namespaces=self.namespace)
                    ),
                    authors=[
                        author.findtext("atom:name", default="", namespaces=self.namespace)
                        for author in entry.findall("atom:author", self.namespace)
                    ],
                    url=entry_url,
                    pdf_url=links.get("application/pdf"),
                    arxiv_id=arxiv_id,
                    sources=[self.name],
                    queries=[query.query],
                    open_access=True,
                    evidence_grade="abstract",
                )
            )
        return SourceResult(source=self.name, query=query.query, papers=papers)


class OpenAlexSource(JsonSource):
    name = "openalex"

    def __init__(self, api_key: str | None) -> None:
        super().__init__(requests_per_second=1)
        self.api_key = api_key

    async def search(self, query: SearchQuery, limit: int = 10) -> SourceResult:
        params: dict[str, Any] = {"search": query.query, "per-page": min(limit, 50)}
        if self.api_key:
            params["api_key"] = self.api_key
        payload = await self.get_json("https://api.openalex.org/works", params=params)
        papers = []
        for item in payload.get("results", []):
            best_oa = item.get("best_oa_location") or {}
            papers.append(
                CandidatePaper(
                    title=item.get("display_name") or "Untitled",
                    abstract=_reconstruct_openalex_abstract(item.get("abstract_inverted_index")),
                    year=item.get("publication_year"),
                    authors=_authors(
                        [auth.get("author", {}) for auth in item.get("authorships", [])]
                    ),
                    venue=((item.get("primary_location") or {}).get("source") or {}).get(
                        "display_name"
                    ),
                    url=item.get("doi") or item.get("id"),
                    pdf_url=best_oa.get("pdf_url"),
                    doi=(item.get("doi") or "").removeprefix("https://doi.org/") or None,
                    openalex_id=(item.get("id") or "").rsplit("/", 1)[-1] or None,
                    reference_ids=[
                        f"openalex:{value.rsplit('/', 1)[-1]}"
                        for value in item.get("referenced_works", [])
                    ],
                    citation_count=item.get("cited_by_count"),
                    open_access=bool((item.get("open_access") or {}).get("is_oa")),
                    sources=[self.name],
                    queries=[query.query],
                    evidence_grade="abstract"
                    if item.get("abstract_inverted_index")
                    else "metadata",
                )
            )
        return SourceResult(source=self.name, query=query.query, papers=papers)


def _reconstruct_openalex_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    words = sorted(
        ((position, word) for word, positions in index.items() for position in positions)
    )
    return " ".join(word for _, word in words)


class CrossrefSource(JsonSource):
    name = "crossref"

    def __init__(self, mailto: str) -> None:
        super().__init__(requests_per_second=2)
        self.mailto = mailto

    async def search(self, query: SearchQuery, limit: int = 10) -> SourceResult:
        payload = await self.get_json(
            "https://api.crossref.org/works",
            params={"query.bibliographic": query.query, "rows": limit, "mailto": self.mailto},
            headers={"User-Agent": f"PaperResearch/0.1 (mailto:{self.mailto})"},
        )
        papers = []
        for item in (payload.get("message") or {}).get("items", []):
            title = (item.get("title") or ["Untitled"])[0]
            year_parts = (item.get("published-print") or item.get("published-online") or {}).get(
                "date-parts"
            ) or [[None]]
            papers.append(
                CandidatePaper(
                    title=title,
                    abstract=re.sub(r"<[^>]+>", " ", item.get("abstract") or "").strip(),
                    year=year_parts[0][0],
                    authors=[
                        " ".join(filter(None, [a.get("given"), a.get("family")]))
                        for a in item.get("author", [])
                    ],
                    venue=(item.get("container-title") or [None])[0],
                    url=item.get("URL") or f"https://doi.org/{item['DOI']}",
                    doi=item.get("DOI"),
                    citation_count=item.get("is-referenced-by-count"),
                    sources=[self.name],
                    queries=[query.query],
                    evidence_grade="abstract" if item.get("abstract") else "metadata",
                )
            )
        return SourceResult(source=self.name, query=query.query, papers=papers)


class DblpSource(JsonSource):
    name = "dblp"

    def __init__(self) -> None:
        super().__init__(requests_per_second=1)

    async def search(self, query: SearchQuery, limit: int = 10) -> SourceResult:
        payload = await self.get_json(
            "https://dblp.org/search/publ/api",
            params={"q": query.query, "h": limit, "format": "json"},
            headers={"User-Agent": "PaperResearch/0.1"},
        )
        raw_hits = ((payload.get("result") or {}).get("hits") or {}).get("hit") or []
        papers = []
        for hit in raw_hits:
            item = hit.get("info") or {}
            raw_authors = (item.get("authors") or {}).get("author") or []
            if isinstance(raw_authors, dict):
                raw_authors = [raw_authors]
            papers.append(
                CandidatePaper(
                    title=re.sub(r"<[^>]+>", "", item.get("title") or "Untitled"),
                    year=_year(item.get("year")),
                    authors=[a.get("text") if isinstance(a, dict) else str(a) for a in raw_authors],
                    venue=item.get("venue"),
                    url=item.get("url")
                    or item.get("ee")
                    or "https://dblp.org/search?q=" + quote_plus(query.query),
                    doi=(item.get("doi") or None),
                    sources=[self.name],
                    queries=[query.query],
                )
            )
        return SourceResult(source=self.name, query=query.query, papers=papers)


class OpenReviewSource(JsonSource):
    name = "openreview"

    def __init__(self) -> None:
        super().__init__(requests_per_second=1)

    async def search(self, query: SearchQuery, limit: int = 10) -> SourceResult:
        used_v1 = False
        try:
            payload = await self.get_json(
                "https://api2.openreview.net/notes",
                params={"content.title": query.query, "limit": limit, "details": "replyCount"},
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code not in {400, 401, 403, 404}:
                raise
            payload = await self.get_json(
                "https://api.openreview.net/notes",
                params={"content.title": query.query, "limit": limit},
            )
            used_v1 = True
        if not payload.get("notes") and not used_v1:
            payload = await self.get_json(
                "https://api.openreview.net/notes",
                params={"content.title": query.query, "limit": limit},
            )
        papers = []
        for note in payload.get("notes", []):
            content = note.get("content") or {}

            def value(key: str, source: dict[str, Any] = content) -> Any:
                raw = source.get(key)
                return raw.get("value") if isinstance(raw, dict) else raw

            title = value("title")
            if not title:
                continue
            note_id = note.get("id")
            papers.append(
                CandidatePaper(
                    title=title,
                    abstract=value("abstract") or "",
                    year=_year(note.get("cdate") or value("date")),
                    authors=value("authors") or [],
                    venue=value("venue"),
                    url=f"https://openreview.net/forum?id={note_id}",
                    pdf_url=f"https://openreview.net/pdf?id={note_id}",
                    openreview_id=note_id,
                    open_access=True,
                    sources=[self.name],
                    queries=[query.query],
                    evidence_grade="abstract" if value("abstract") else "metadata",
                )
            )
        return SourceResult(source=self.name, query=query.query, papers=papers)


def build_sources(settings: Settings) -> list[LiteratureSource]:
    return [
        ArxivSource(),
        OpenReviewSource(),
        OpenAlexSource(Settings.reveal(settings.OPENALEX_API_KEY)),
        CrossrefSource(settings.CROSSREF_MAILTO),
        DblpSource(),
    ]
