from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from ..models import Job, JobFile, JobStatus, ProviderUsage
from ..security import redact

LOGGER = logging.getLogger(__name__)


def _jpeg_size(content: bytes) -> tuple[int, int]:
    index = 2
    while index + 9 < len(content):
        if content[index] != 0xFF:
            index += 1
            continue
        marker = content[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(content):
            break
        length = int.from_bytes(content[index:index + 2], "big")
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            return (
                int.from_bytes(content[index + 5:index + 7], "big"),
                int.from_bytes(content[index + 3:index + 5], "big"),
            )
        index += max(2, length)
    raise ValueError("Could not read JPEG dimensions")


def _postgres_json(value: Any) -> Any:
    """Remove values that PostgreSQL jsonb cannot safely accept."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _postgres_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_postgres_json(item) for item in value]
    return value


class SupabaseRepository:
    """Small service-role PostgREST/Storage client used by the outbound worker."""

    def __init__(
        self, url: str, service_key: str, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self.url = url.rstrip("/")
        self.service_key = service_key
        self._client = client or httpx.AsyncClient(timeout=60)
        self._owns_client = client is None

    @property
    def headers(self) -> dict[str, str]:
        return {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Content-Type": "application/json",
        }

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {**self.headers, **kwargs.pop("headers", {})}
        if "json" in kwargs:
            kwargs["json"] = _postgres_json(kwargs["json"])
        response = await self._client.request(
            method, f"{self.url}{path}", headers=headers, **kwargs
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            diagnostic = redact(response.text.strip())[-800:]
            detail = f"; response={diagnostic}" if diagnostic else ""
            raise httpx.HTTPStatusError(
                f"{error}{detail}", request=response.request, response=response
            ) from error
        return response

    async def claim_next_job(self, worker_id: str, lease_seconds: int) -> Job | None:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/claim_next_job",
            json={"p_worker_id": worker_id, "p_lease_seconds": lease_seconds},
        )
        rows = response.json()
        if not rows:
            return None
        row = rows[0] if isinstance(rows, list) else rows
        files_response = await self._request(
            "GET",
            f"/rest/v1/job_files?job_id=eq.{quote(str(row['id']))}&select=upload:uploads(*)",
        )
        files = [
            JobFile(
                id=item["upload"]["id"],
                storage_path=item["upload"]["storage_path"],
                original_name=item["upload"]["original_name"],
                size_bytes=item["upload"]["size_bytes"],
                sha256=item["upload"].get("sha256"),
            )
            for item in files_response.json()
        ]
        return Job(
            id=row["id"],
            user_id=row["user_id"],
            mode=row["mode"],
            max_rounds=row["max_rounds"],
            languages=row.get("languages") or ["zh", "en"],
            status=row["status"],
            current_round=row.get("current_round", 0),
            stage=row.get("stage", "queued"),
            research_brief=row.get("research_brief") or "",
            checkpoint=row.get("checkpoint") or {},
            files=files,
        )

    async def update_job(self, job_id: str, **values: Any) -> None:
        await self._request(
            "PATCH",
            f"/rest/v1/jobs?id=eq.{quote(job_id)}",
            headers={"Prefer": "return=minimal"},
            json=values,
        )

    async def renew_lease(self, job_id: str, worker_id: str, lease_seconds: int) -> None:
        await self._request(
            "POST",
            "/rest/v1/rpc/renew_job_lease",
            json={"p_job_id": job_id, "p_worker_id": worker_id, "p_lease_seconds": lease_seconds},
        )

    async def update_upload_hash(self, upload_id: str, sha256: str) -> None:
        await self._request(
            "PATCH",
            f"/rest/v1/uploads?id=eq.{quote(upload_id)}",
            headers={"Prefer": "return=minimal"},
            json={"sha256": sha256, "status": "validated"},
        )

    async def load_analysis_state(self, job_id: str) -> dict[str, list[dict[str, Any]]]:
        encoded = quote(job_id)
        problems, candidates, rounds = await asyncio.gather(
            self._request(
                "GET",
                f"/rest/v1/problem_statements?job_id=eq.{encoded}&select=paper_id,content",
            ),
            self._request(
                "GET",
                f"/rest/v1/candidate_papers?job_id=eq.{encoded}&select=content",
            ),
            self._request(
                "GET",
                f"/rest/v1/search_runs?job_id=eq.{encoded}&select=round_number,queries,analysis&order=round_number",
            ),
        )
        return {
            "problems": problems.json(),
            "candidates": candidates.json(),
            "rounds": rounds.json(),
        }

    async def load_pipeline_checkpoint(self, job_id: str) -> dict[str, Any]:
        response = await self._request(
            "GET", f"/rest/v1/jobs?id=eq.{quote(job_id)}&select=checkpoint"
        )
        rows = response.json()
        return dict((rows[0].get("checkpoint") if rows else None) or {})

    async def save_pipeline_checkpoint(
        self, job_id: str, checkpoint: dict[str, Any]
    ) -> None:
        await self.update_job(job_id, checkpoint=checkpoint)

    async def cleanup_expired(self) -> dict[str, int]:
        response, preview_response = await asyncio.gather(
            self._request("POST", "/rest/v1/rpc/claim_expired_storage", json={}),
            self._request("POST", "/rest/v1/rpc/claim_expired_preview_storage", json={}),
        )
        rows = response.json() or []
        upload_rows = [row for row in rows if row.get("kind") == "upload"]
        orphan_rows = [row for row in rows if row.get("kind") == "orphan"]
        paths = list(
            dict.fromkeys(
                row["storage_path"]
                for row in upload_rows + orphan_rows
                if row.get("storage_path")
            )
        )
        if paths:
            await self._request("DELETE", "/storage/v1/object/papers", json={"prefixes": paths})
            ids = ",".join(quote(row["record_id"]) for row in upload_rows)
            await self._request(
                "PATCH",
                f"/rest/v1/uploads?id=in.({ids})",
                headers={"Prefer": "return=minimal"},
                json={"status": "deleted"},
            )
        if orphan_rows:
            ids = ",".join(quote(row["record_id"]) for row in orphan_rows)
            await self._request(
                "DELETE",
                f"/rest/v1/storage_deletion_queue?id=in.({ids})",
                headers={"Prefer": "return=minimal"},
            )
        preview_rows = preview_response.json() or []
        preview_paths = [row["storage_path"] for row in preview_rows if row.get("storage_path")]
        if preview_paths:
            await self._request(
                "DELETE", "/storage/v1/object/evidence-previews", json={"prefixes": preview_paths}
            )
            ids = ",".join(quote(row["record_id"]) for row in preview_rows)
            await self._request(
                "DELETE",
                f"/rest/v1/storage_deletion_queue?id=in.({ids})",
                headers={"Prefer": "return=minimal"},
            )
        return {
            "uploads": len(upload_rows),
            "orphans": len(orphan_rows),
            "reports": sum(1 for row in rows if row.get("kind") == "report"),
            "previews": len(preview_rows),
        }

    async def add_event(
        self, job_id: str, kind: str, message: str, data: dict[str, Any] | None = None
    ) -> None:
        await self._request(
            "POST",
            "/rest/v1/job_events",
            headers={"Prefer": "return=minimal"},
            json={"job_id": job_id, "kind": kind, "message": message, "data": data or {}},
        )

    async def is_cancelled(self, job_id: str) -> bool:
        response = await self._request(
            "GET", f"/rest/v1/jobs?id=eq.{quote(job_id)}&select=cancellation_requested,status"
        )
        rows = response.json()
        return bool(
            rows and (rows[0]["cancellation_requested"] or rows[0]["status"] == "cancelled")
        )

    async def download_upload(self, storage_path: str, destination: Path) -> None:
        encoded_path = "/".join(quote(part, safe="") for part in storage_path.split("/"))
        response = await self._request("GET", f"/storage/v1/object/papers/{encoded_path}")
        if len(response.content) > 50 * 1024 * 1024:
            raise ValueError("PDF exceeds the 50 MB worker limit")
        destination.write_bytes(response.content)

    async def delete_uploads(self, files: list[JobFile]) -> None:
        paths = [item.storage_path for item in files]
        if paths:
            await self._request("DELETE", "/storage/v1/object/papers", json={"prefixes": paths})
        ids = ",".join(quote(item.id) for item in files)
        if ids:
            await self._request(
                "PATCH",
                f"/rest/v1/uploads?id=in.({ids})",
                headers={"Prefer": "return=minimal"},
                json={"status": "deleted"},
            )

    async def register_input_asset(
        self,
        job_id: str,
        file: JobFile,
        paper_id: str,
        metadata: dict[str, Any],
    ) -> str:
        response = await self._request(
            "POST",
            "/rest/v1/report_evidence_assets?on_conflict=job_id,paper_id,source_kind",
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            json={
                "job_id": job_id,
                "upload_id": file.id,
                "paper_id": paper_id,
                "source_kind": "input",
                "storage_path": file.storage_path,
                "original_name": file.original_name,
                "sha256": paper_id,
                "metadata": _postgres_json(metadata),
            },
        )
        return str(response.json()[0]["id"])

    async def upload_external_asset(
        self,
        job_id: str,
        paper_id: str,
        title: str,
        source_url: str,
        pdf_path: Path,
        metadata: dict[str, Any],
        *,
        license_name: str | None = None,
    ) -> str:
        content = pdf_path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        storage_path = f"evidence/{job_id}/{digest}.pdf"
        encoded_path = "/".join(quote(part, safe="") for part in storage_path.split("/"))
        await self._request(
            "POST",
            f"/storage/v1/object/papers/{encoded_path}",
            headers={"Content-Type": "application/pdf", "x-upsert": "true"},
            content=content,
        )
        response = await self._request(
            "POST",
            "/rest/v1/report_evidence_assets?on_conflict=job_id,paper_id,source_kind",
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            json={
                "job_id": job_id,
                "paper_id": paper_id,
                "source_kind": "external",
                "storage_path": storage_path,
                "original_name": f"{title[:140]}.pdf",
                "sha256": digest,
                "source_url": source_url,
                "license": license_name,
                "metadata": _postgres_json(metadata),
            },
        )
        return str(response.json()[0]["id"])

    async def update_evidence_asset_metadata(
        self, asset_id: str, metadata: dict[str, Any]
    ) -> None:
        await self._request(
            "PATCH",
            f"/rest/v1/report_evidence_assets?id=eq.{quote(asset_id)}",
            headers={"Prefer": "return=minimal"},
            json={"metadata": _postgres_json(metadata)},
        )

    async def generate_evidence_previews(
        self, job_id: str, workspace: Path, *, concurrency: int = 2
    ) -> int:
        """Pre-render cited PDF pages so evidence opens before PDF.js downloads."""
        renderer = shutil.which("pdftoppm")
        if not renderer:
            return 0
        response = await self._request(
            "GET",
            "/rest/v1/report_evidence_assets"
            f"?job_id=eq.{quote(job_id)}&select=id,storage_path,metadata",
        )
        assets = response.json()
        preview_root = workspace / "evidence-previews"
        preview_root.mkdir(parents=True, exist_ok=True)
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def render_asset(asset: dict[str, Any]) -> int:
            locators = (asset.get("metadata") or {}).get("evidence_locators") or []
            cited_pages = sorted(
                {
                    int(item["page"])
                    for item in locators
                    if isinstance(item, dict) and isinstance(item.get("page"), int)
                    and int(item["page"]) > 0
                }
            )
            asset_id = str(asset["id"])
            existing_response = await self._request(
                "GET",
                "/rest/v1/report_evidence_previews"
                f"?asset_id=eq.{quote(asset_id)}&select=page",
            )
            existing_pages = {
                int(row["page"])
                for row in existing_response.json()
                if isinstance(row.get("page"), int)
            }
            pages = [page for page in cited_pages if page not in existing_pages]
            if not pages:
                return 0
            async with semaphore:
                pdf_path = preview_root / f"{asset_id}.pdf"
                encoded_path = "/".join(
                    quote(part, safe="") for part in str(asset["storage_path"]).split("/")
                )
                pdf_response = await self._request(
                    "GET", f"/storage/v1/object/papers/{encoded_path}"
                )
                pdf_path.write_bytes(pdf_response.content)
                rows: list[dict[str, Any]] = []
                try:
                    for page in pages:
                        output_base = preview_root / f"{asset_id}-{page}"
                        process = await asyncio.create_subprocess_exec(
                            renderer,
                            "-f", str(page),
                            "-l", str(page),
                            "-singlefile",
                            "-jpeg",
                            "-r", "110",
                            "-jpegopt", "quality=72,progressive=y,optimize=y",
                            str(pdf_path),
                            str(output_base),
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        _, stderr = await process.communicate()
                        image_path = output_base.with_suffix(".jpg")
                        if process.returncode or not image_path.exists():
                            LOGGER.warning(
                                "Evidence preview failed for asset %s page %s: %s",
                                asset_id, page, redact(stderr.decode(errors="ignore"))[-300:],
                            )
                            continue
                        content = image_path.read_bytes()
                        width, height = _jpeg_size(content)
                        storage_path = f"{job_id}/{asset_id}/{page}.jpg"
                        encoded_preview = "/".join(
                            quote(part, safe="") for part in storage_path.split("/")
                        )
                        await self._request(
                            "POST",
                            f"/storage/v1/object/evidence-previews/{encoded_preview}",
                            headers={"Content-Type": "image/jpeg", "x-upsert": "true"},
                            content=content,
                        )
                        rows.append(
                            {
                                "asset_id": asset_id,
                                "page": page,
                                "storage_path": storage_path,
                                "width": width,
                                "height": height,
                                "byte_size": len(content),
                            }
                        )
                        image_path.unlink(missing_ok=True)
                finally:
                    pdf_path.unlink(missing_ok=True)
                if rows:
                    await self._request(
                        "POST",
                        "/rest/v1/report_evidence_previews?on_conflict=asset_id,page",
                        headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                        json=rows,
                    )
                return len(rows)

        counts = await asyncio.gather(*(render_asset(asset) for asset in assets))
        return sum(counts)

    async def load_external_profiles(self, job_id: str) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            "/rest/v1/report_evidence_assets"
            f"?job_id=eq.{quote(job_id)}&source_kind=eq.external&select=metadata",
        )
        return [
            profile
            for row in response.json()
            if isinstance((profile := (row.get("metadata") or {}).get("profile")), dict)
        ]

    async def prune_external_assets(
        self, job_id: str, keep_paper_ids: set[str]
    ) -> int:
        """Queue cached external PDFs not used by the final evidence landscape."""
        response = await self._request(
            "GET",
            "/rest/v1/report_evidence_assets"
            f"?job_id=eq.{quote(job_id)}&source_kind=eq.external&select=id,paper_id",
        )
        stale_ids = [
            str(row["id"])
            for row in response.json()
            if str(row.get("paper_id") or "") not in keep_paper_ids
        ]
        if not stale_ids:
            return 0
        encoded_ids = ",".join(quote(asset_id) for asset_id in stale_ids)
        await self._request(
            "DELETE",
            f"/rest/v1/report_evidence_assets?id=in.({encoded_ids})",
            headers={"Prefer": "return=minimal"},
        )
        return len(stale_ids)

    async def save_problem_statement(
        self, job_id: str, paper_id: str, payload: dict[str, Any]
    ) -> None:
        await self._request(
            "POST",
            "/rest/v1/problem_statements?on_conflict=job_id,paper_id",
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"job_id": job_id, "paper_id": paper_id, "content": payload},
        )

    async def save_search_round(
        self, job_id: str, round_number: int, query_bundle: dict[str, Any], analysis: dict[str, Any]
    ) -> None:
        await self._request(
            "POST",
            "/rest/v1/search_runs?on_conflict=job_id,round_number",
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json={
                "job_id": job_id,
                "round_number": round_number,
                "queries": query_bundle,
                "analysis": analysis,
            },
        )

    async def save_candidates(self, job_id: str, candidates: list[dict[str, Any]]) -> None:
        if not candidates:
            return
        rows = [
            {"job_id": job_id, "canonical_id": item["canonical_id"], "content": item}
            for item in candidates
        ]
        await self._request(
            "POST",
            "/rest/v1/candidate_papers?on_conflict=job_id,canonical_id",
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=rows,
        )

    async def save_report(
        self,
        job_id: str,
        payload: dict[str, Any],
        markdown: str,
        summary: dict[str, Any] | None = None,
        sections: dict[str, dict[str, Any]] | None = None,
    ) -> str:
        response = await self._request(
            "POST",
            "/rest/v1/reports?on_conflict=job_id",
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            json={"job_id": job_id, "content": payload, "markdown": markdown, "summary": summary},
        )
        report_id = str(response.json()[0]["id"])
        await self._request(
            "PATCH",
            f"/rest/v1/report_evidence_assets?job_id=eq.{quote(job_id)}",
            headers={"Prefer": "return=minimal"},
            json={"report_id": report_id},
        )
        if sections:
            await self._request(
                "POST",
                "/rest/v1/report_sections?on_conflict=report_id,section",
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                json=[
                    {
                        "report_id": report_id,
                        "section": name,
                        "content": _postgres_json(content),
                    }
                    for name, content in sections.items()
                ],
            )
        return report_id

    async def record_usage(self, job_id: str, usage: ProviderUsage) -> None:
        await self._request(
            "POST",
            "/rest/v1/provider_usage",
            headers={"Prefer": "return=minimal"},
            json={"job_id": job_id, **usage.model_dump(mode="json")},
        )

    async def monthly_spend_cny(self) -> float:
        response = await self._request("POST", "/rest/v1/rpc/current_month_provider_spend", json={})
        value = response.json()
        return float(value or 0)

    async def finish_job(self, job_id: str, status: JobStatus, error: str | None = None) -> None:
        await self._request(
            "POST",
            "/rest/v1/rpc/finish_job",
            json={"p_job_id": job_id, "p_status": status.value, "p_error": error},
        )
