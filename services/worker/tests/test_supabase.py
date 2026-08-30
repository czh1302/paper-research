import math

import httpx
import pytest
from paper_research.clients.supabase import SupabaseRepository, _postgres_json


def test_postgres_json_removes_null_characters_and_non_finite_numbers() -> None:
    payload = {
        "quote": "before\x00after",
        "bboxes": [[0.0, math.nan, math.inf, 1000.0]],
    }

    assert _postgres_json(payload) == {
        "quote": "beforeafter",
        "bboxes": [[0.0, None, None, 1000.0]],
    }


@pytest.mark.asyncio
async def test_load_external_profiles_ignores_assets_without_profile() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["job_id"] == "eq.job-1"
        assert request.url.params["source_kind"] == "eq.external"
        return httpx.Response(
            200,
            json=[
                {"metadata": {"profile": {"paper_id": "paper-1"}}},
                {"metadata": {"evidence_locators": []}},
                {"metadata": None},
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    repository = SupabaseRepository("https://example.test", "service-key", client=client)
    try:
        assert await repository.load_external_profiles("job-1") == [
            {"paper_id": "paper-1"}
        ]
    finally:
        await client.aclose()
