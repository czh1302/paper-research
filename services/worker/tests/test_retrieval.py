import asyncio

import pytest
from paper_research.config import Settings
from paper_research.models import CandidatePaper
from paper_research.sources.academic import build_sources
from paper_research.sources.retriever import canonical_id, merge_candidates, normalize_title
from pydantic import ValidationError


def test_deduplicates_by_doi_and_merges_sources() -> None:
    first = CandidatePaper(title="A Paper", url="https://one", doi="10.1/ABC", sources=["crossref"])
    second = CandidatePaper(
        title="A Paper", url="https://two", doi="10.1/abc", abstract="Useful", sources=["openalex"]
    )
    merged = merge_candidates([first, second])
    assert len(merged) == 1
    assert merged[0].abstract == "Useful"
    assert merged[0].sources == ["crossref", "openalex"]
    assert canonical_id(merged[0]) == "doi:10.1/abc"


def test_title_normalization_is_stable() -> None:
    assert normalize_title("Attention Is All You Need!") == "attentionisallyouneed"


def test_candidate_rejects_non_http_links() -> None:
    with pytest.raises(ValidationError):
        CandidatePaper(title="Unsafe", url="javascript:alert(1)")


def test_academic_source_set_is_expected() -> None:
    sources = build_sources(Settings())

    async def close_sources() -> None:
        await asyncio.gather(*(source.close() for source in sources))

    try:
        assert [source.name for source in sources] == [
            "arxiv",
            "openreview",
            "openalex",
            "crossref",
            "dblp",
        ]
    finally:
        asyncio.run(close_sources())
