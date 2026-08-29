from __future__ import annotations

import asyncio
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..security import safe_filename, validate_public_url


class MinerUError(RuntimeError):
    pass


class MinerUClient:
    """MinerU Precision Extract client using signed file uploads."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://mineru.net",
        model: str = "vlm",
        poll_seconds: float = 5,
        timeout_seconds: int = 900,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(60, connect=20),
            follow_redirects=True,
        )
        self._owns_client = client is None

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    async def _request_upload(self, file_path: Path, data_id: str) -> tuple[str, str]:
        payload = {
            "files": [
                {
                    "name": safe_filename(file_path.name),
                    "data_id": data_id[:128],
                    "is_ocr": False,
                    "page_ranges": "1-100",
                }
            ],
            "model_version": self.model,
            "enable_formula": True,
            "enable_table": True,
            "language": "en",
        }
        response = await self._client.post(
            f"{self.base_url}/api/v4/file-urls/batch",
            headers=self.headers,
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 0:
            raise MinerUError(f"MinerU upload request failed: {body.get('msg', 'unknown error')}")
        data = body.get("data") or {}
        urls = data.get("file_urls") or []
        if not data.get("batch_id") or len(urls) != 1:
            raise MinerUError("MinerU returned an invalid signed-upload response")
        return str(data["batch_id"]), validate_public_url(str(urls[0]))

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    async def _upload(self, upload_url: str, file_path: Path) -> None:
        content = await asyncio.to_thread(file_path.read_bytes)
        response = await self._client.put(upload_url, content=content)
        response.raise_for_status()

    async def _poll(self, batch_id: str, data_id: str) -> str:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            response = await self._client.get(
                f"{self.base_url}/api/v4/extract-results/batch/{batch_id}",
                headers=self.headers,
            )
            response.raise_for_status()
            body = response.json()
            if body.get("code") != 0:
                raise MinerUError(f"MinerU polling failed: {body.get('msg', 'unknown error')}")
            results = (body.get("data") or {}).get("extract_result") or []
            match = next((item for item in results if item.get("data_id") == data_id), None)
            match = match or (results[0] if len(results) == 1 else None)
            if match:
                state = match.get("state")
                if state == "done":
                    url = match.get("full_zip_url")
                    if not url:
                        raise MinerUError("MinerU completed without an archive URL")
                    return validate_public_url(str(url))
                if state == "failed":
                    raise MinerUError(
                        f"MinerU extraction failed: {match.get('err_msg', 'unknown error')}"
                    )
            await asyncio.sleep(self.poll_seconds)
        raise TimeoutError(f"MinerU extraction timed out after {self.timeout_seconds} seconds")

    async def _download(self, archive_url: str, destination: Path) -> None:
        response = await self._client.get(archive_url)
        response.raise_for_status()
        if len(response.content) > 500 * 1024 * 1024:
            raise MinerUError("MinerU archive exceeded the 500 MB safety limit")
        destination.write_bytes(response.content)

    async def _precision_extract(self, file_path: Path, data_id: str, output_dir: Path) -> Path:
        batch_id, upload_url = await self._request_upload(file_path, data_id)
        await self._upload(upload_url, file_path)
        archive_url = await self._poll(batch_id, data_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        archive_path = output_dir / f"{data_id}.zip"
        await self._download(archive_url, archive_path)
        return archive_path

    async def _flash_extract(self, file_path: Path, data_id: str, output_dir: Path) -> Path:
        response = await self._client.post(
            f"{self.base_url}/api/v1/agent/parse/file",
            json={
                "file_name": safe_filename(file_path.name),
                "language": "en",
                "page_range": "1-20",
                "enable_table": True,
                "enable_formula": True,
                "is_ocr": False,
            },
        )
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 0:
            raise MinerUError(f"MinerU Flash submission failed: {body.get('msg', 'unknown error')}")
        data = body.get("data") or {}
        task_id, upload_url = data.get("task_id"), data.get("file_url")
        if not task_id or not upload_url:
            raise MinerUError("MinerU Flash returned an invalid signed-upload response")
        await self._upload(validate_public_url(str(upload_url)), file_path)
        deadline = time.monotonic() + min(self.timeout_seconds, 300)
        markdown_url = None
        while time.monotonic() < deadline:
            status = await self._client.get(f"{self.base_url}/api/v1/agent/parse/{task_id}")
            status.raise_for_status()
            payload = status.json()
            state = (payload.get("data") or {}).get("state")
            if state == "done":
                markdown_url = (payload.get("data") or {}).get("markdown_url")
                break
            if state == "failed":
                raise MinerUError(
                    f"MinerU Flash extraction failed: {(payload.get('data') or {}).get('err_msg', 'unknown error')}"
                )
            await asyncio.sleep(self.poll_seconds)
        if not markdown_url:
            raise TimeoutError("MinerU Flash extraction timed out")
        markdown_response = await self._client.get(validate_public_url(str(markdown_url)))
        markdown_response.raise_for_status()
        output_dir.mkdir(parents=True, exist_ok=True)
        archive_path = output_dir / f"{data_id}-flash.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("full.md", markdown_response.text)
        return archive_path

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception_type((MinerUError, httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    async def _precision_with_retry(self, file_path: Path, data_id: str, output_dir: Path) -> Path:
        return await self._precision_extract(file_path, data_id, output_dir)

    async def extract(self, file_path: Path, data_id: str, output_dir: Path) -> Path:
        try:
            return await self._precision_with_retry(file_path, data_id, output_dir)
        except Exception as precision_error:
            if file_path.stat().st_size > 10 * 1024 * 1024:
                raise MinerUError(
                    f"Precision extraction failed and the file is too large for Flash: {precision_error}"
                ) from precision_error
            try:
                return await self._flash_extract(file_path, data_id, output_dir)
            except Exception as flash_error:
                raise MinerUError(
                    f"Precision and Flash extraction both failed: precision={precision_error}; flash={flash_error}"
                ) from flash_error


def parse_mineru_result(payload: dict[str, Any], data_id: str) -> dict[str, Any] | None:
    results = (payload.get("data") or {}).get("extract_result") or []
    return next((item for item in results if item.get("data_id") == data_id), None)
