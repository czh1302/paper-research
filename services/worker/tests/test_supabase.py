import json
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
async def test_claimed_job_files_follow_database_position_not_response_order() -> None:
    def upload(number: int) -> dict[str, object]:
        return {
            "id": f"upload-{number}",
            "storage_path": f"user/upload-{number}.pdf",
            "original_name": f"paper-{number}.pdf",
            "size_bytes": 100,
            "sha256": f"sha-{number}",
        }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/rpc/claim_next_job"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "job-1",
                        "user_id": "user-1",
                        "mode": "multi",
                        "max_rounds": 1,
                        "status": "queued",
                    }
                ],
            )
        assert request.url.path.endswith("/job_files")
        assert request.url.params["order"] == "position.asc"
        assert "position" in request.url.params["select"]
        return httpx.Response(
            200,
            json=[
                {"position": 2, "upload": upload(2)},
                {"position": 1, "upload": upload(1)},
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    repository = SupabaseRepository("https://example.test", "service-key", client=client)
    try:
        job = await repository.claim_next_job("worker-1", 300)
        assert job is not None
        assert [item.id for item in job.files] == ["upload-1", "upload-2"]
        assert [item.position for item in job.files] == [1, 2]
    finally:
        await client.aclose()

@pytest.mark.asyncio
async def test_all_repository_json_requests_remove_null_characters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body[0]["content"]["abstract"] == "beforeafter"
        return httpx.Response(201)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    repository = SupabaseRepository("https://example.test", "service-key", client=client)
    try:
        await repository.save_candidates(
            "job-1",
            [{"canonical_id": "paper-1", "abstract": "before\x00after"}],
        )
    finally:
        await client.aclose()


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


@pytest.mark.asyncio
async def test_external_asset_path_is_unique_per_canonical_paper(tmp_path) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/storage/v1/object/papers/evidence/"):
            paths.append(request.url.path)
            return httpx.Response(200)
        assert request.url.path == "/rest/v1/report_evidence_assets"
        assert request.url.params["on_conflict"] == "job_id,paper_id,source_kind"
        return httpx.Response(201, json=[{"id": f"asset-{len(paths)}"}])

    pdf = tmp_path / "shared.pdf"
    pdf.write_bytes(b"%PDF-1.4\nshared bytes")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    repository = SupabaseRepository("https://example.test", "service-key", client=client)
    try:
        await repository.upload_external_asset(
            "job-1", "paper-a", "Paper A", "https://example.test/a", pdf, {}
        )
        await repository.upload_external_asset(
            "job-1", "paper-b", "Paper B", "https://example.test/b", pdf, {}
        )
    finally:
        await client.aclose()

    assert len(paths) == 2
    assert paths[0] != paths[1]


@pytest.mark.asyncio
async def test_prune_external_assets_deletes_only_unselected_rows() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            assert request.url.params["job_id"] == "eq.job-1"
            assert request.url.params["source_kind"] == "eq.external"
            return httpx.Response(
                200,
                json=[
                    {"id": "asset-keep", "paper_id": "paper-keep"},
                    {"id": "asset-drop", "paper_id": "paper-drop"},
                ],
            )
        assert request.method == "DELETE"
        assert request.url.params["id"] == "in.(asset-drop)"
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    repository = SupabaseRepository("https://example.test", "service-key", client=client)
    try:
        assert await repository.prune_external_assets("job-1", {"paper-keep"}) == 1
        assert [request.method for request in requests] == ["GET", "DELETE"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_permanent_job_delete_removes_storage_before_transactional_purge() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/rest/v1/jobs":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "job-1",
                        "job_files": [
                            {"upload": {"id": "upload-1", "storage_path": "user/upload.pdf"}}
                        ],
                    }
                ],
            )
        if path == "/rest/v1/report_evidence_assets":
            return httpx.Response(
                200,
                json=[{"id": "asset-1", "storage_path": "evidence/external.pdf"}],
            )
        if path == "/rest/v1/report_evidence_previews":
            return httpx.Response(
                200,
                json=[{"storage_path": "job/asset/page-1.jpg"}],
            )
        if path.startswith("/storage/v1/object/"):
            return httpx.Response(200)
        if path == "/rest/v1/rpc/purge_job_records":
            assert json.loads(request.content) == {"p_job_id": "job-1"}
            return httpx.Response(204)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    repository = SupabaseRepository("https://example.test", "service-key", client=client)
    try:
        await repository.delete_job_permanently("job-1")
        assert requests[-1].url.path == "/rest/v1/rpc/purge_job_records"
        storage_requests = [request for request in requests if request.url.path.startswith("/storage/")]
        assert len(storage_requests) == 2
    finally:
        await client.aclose()
