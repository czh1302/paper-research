import asyncio
import io
import zipfile
from pathlib import Path

import httpx
import pytest
from paper_research.clients.mineru import MinerUClient


@pytest.mark.asyncio
async def test_precision_extract_signed_upload_flow(tmp_path: Path) -> None:
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("full.md", "# Parsed paper")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v4/file-urls/batch":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": "batch-1",
                        "file_urls": ["https://upload.example/signed"],
                    },
                },
            )
        if request.method == "PUT" and request.url.host == "upload.example":
            return httpx.Response(200)
        if request.url.path == "/api/v4/extract-results/batch/batch-1":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": "paper-1",
                                "state": "done",
                                "full_zip_url": "https://download.example/result.zip",
                            }
                        ]
                    },
                },
            )
        if request.url.host == "download.example":
            return httpx.Response(200, content=archive_buffer.getvalue())
        return httpx.Response(404)

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF- mock")
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MinerUClient("test-token", client=http_client, poll_seconds=0.01)
    try:
        result = await asyncio.wait_for(
            client._precision_extract(pdf, "paper-1", tmp_path / "output"), timeout=2
        )
        assert result.exists()
        with zipfile.ZipFile(result) as archive:
            assert archive.read("full.md") == b"# Parsed paper"
    finally:
        await http_client.aclose()
