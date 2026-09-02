from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from ..experiment_models import ExperimentRecord, ExperimentStatus
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
            f"/rest/v1/job_files?job_id=eq.{quote(str(row['id']))}"
            "&select=position,upload:uploads(*)&order=position.asc",
        )
        files = [
            JobFile(
                id=item["upload"]["id"],
                storage_path=item["upload"]["storage_path"],
                original_name=item["upload"]["original_name"],
                size_bytes=item["upload"]["size_bytes"],
                sha256=item["upload"].get("sha256"),
                position=int(item["position"]),
            )
            for item in sorted(
                files_response.json(), key=lambda value: int(value["position"])
            )
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
            retry_count=int(row.get("retry_count") or 0),
            next_retry_at=row.get("next_retry_at"),
            last_recovery_at=row.get("last_recovery_at"),
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
        response, preview_response, experiment_response, chat_response = await asyncio.gather(
            self._request("POST", "/rest/v1/rpc/claim_expired_storage", json={}),
            self._request("POST", "/rest/v1/rpc/claim_expired_preview_storage", json={}),
            self._request(
                "POST", "/rest/v1/rpc/claim_expired_experiment_storage", json={}
            ),
            self._request(
                "POST", "/rest/v1/rpc/claim_expired_experiment_chat_storage", json={}
            ),
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
        experiment_rows = experiment_response.json() or []
        experiment_paths = [
            row["storage_path"]
            for row in experiment_rows
            if row.get("storage_path")
        ]
        if experiment_paths:
            await self._request(
                "DELETE",
                "/storage/v1/object/experiment-artifacts",
                json={"prefixes": experiment_paths},
            )
            ids = ",".join(quote(row["record_id"]) for row in experiment_rows)
            await self._request(
                "DELETE",
                f"/rest/v1/storage_deletion_queue?id=in.({ids})",
                headers={"Prefer": "return=minimal"},
            )
        chat_rows = chat_response.json() or []
        chat_paths = [row["storage_path"] for row in chat_rows if row.get("storage_path")]
        if chat_paths:
            await self._request(
                "DELETE",
                "/storage/v1/object/experiment-chat-attachments",
                json={"prefixes": chat_paths},
            )
            ids = ",".join(quote(row["record_id"]) for row in chat_rows)
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
            "experiment_artifacts": len(experiment_rows),
            "experiment_chat_attachments": len(chat_rows),
        }

    async def claim_admin_deletion_request(
        self, worker_id: str, lease_seconds: int
    ) -> dict[str, Any] | None:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/claim_admin_deletion_request",
            json={"p_worker_id": worker_id, "p_lease_seconds": lease_seconds},
        )
        rows = response.json() or []
        if not rows:
            return None
        return dict(rows[0] if isinstance(rows, list) else rows)

    async def admin_deletion_target_ready(self, target_kind: str, target_id: str) -> bool:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/admin_deletion_target_ready",
            json={"p_target_kind": target_kind, "p_target_id": target_id},
        )
        return response.json() is True

    async def finish_admin_deletion_request(
        self,
        request_id: str,
        worker_id: str,
        *,
        success: bool,
        retry_seconds: int = 60,
        error: str | None = None,
    ) -> None:
        await self._request(
            "POST",
            "/rest/v1/rpc/finish_admin_deletion_request",
            json={
                "p_request_id": request_id,
                "p_worker_id": worker_id,
                "p_success": success,
                "p_retry_seconds": retry_seconds,
                "p_error": error,
            },
        )

    async def _remove_storage_paths(self, bucket: str, paths: list[str]) -> None:
        unique_paths = list(dict.fromkeys(path for path in paths if path))
        if unique_paths:
            await self._request(
                "DELETE", f"/storage/v1/object/{bucket}", json={"prefixes": unique_paths}
            )

    async def delete_job_permanently(self, job_id: str) -> None:
        encoded_job_id = quote(job_id)
        job_response, asset_response = await asyncio.gather(
            self._request(
                "GET",
                "/rest/v1/jobs"
                f"?id=eq.{encoded_job_id}&select=id,job_files(upload:uploads(id,storage_path))",
            ),
            self._request(
                "GET",
                "/rest/v1/report_evidence_assets"
                f"?job_id=eq.{encoded_job_id}&select=id,storage_path",
            ),
        )
        jobs = job_response.json() or []
        assets = asset_response.json() or []
        upload_rows = [
            item.get("upload")
            for job in jobs
            for item in (job.get("job_files") or [])
            if item.get("upload")
        ]
        asset_ids = [str(item["id"]) for item in assets if item.get("id")]
        previews: list[dict[str, Any]] = []
        if asset_ids:
            encoded_ids = ",".join(quote(item) for item in asset_ids)
            preview_response = await self._request(
                "GET",
                "/rest/v1/report_evidence_previews"
                f"?asset_id=in.({encoded_ids})&select=storage_path",
            )
            previews = preview_response.json() or []

        await self._remove_storage_paths(
            "papers",
            [str(item.get("storage_path") or "") for item in upload_rows]
            + [str(item.get("storage_path") or "") for item in assets],
        )
        await self._remove_storage_paths(
            "evidence-previews",
            [str(item.get("storage_path") or "") for item in previews],
        )
        await self._request(
            "POST",
            "/rest/v1/rpc/purge_job_records",
            json={"p_job_id": job_id},
        )

    async def delete_user_permanently(self, user_id: str) -> None:
        response = await self._request(
            "GET", f"/rest/v1/jobs?user_id=eq.{quote(user_id)}&select=id"
        )
        for row in response.json() or []:
            await self.delete_job_permanently(str(row["id"]))
        try:
            await self._request(
                "DELETE",
                f"/auth/v1/admin/users/{quote(user_id)}?should_soft_delete=false",
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 404:
                raise

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
        # Different discovery sources can assign different canonical paper IDs to
        # the same PDF. Include the paper identity in the object key so the
        # table's per-paper upsert cannot collide with the global storage_path
        # uniqueness constraint.
        paper_digest = hashlib.sha256(paper_id.encode("utf-8")).hexdigest()[:16]
        storage_path = f"evidence/{job_id}/{paper_digest}-{digest}.pdf"
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
            json={
                "job_id": job_id,
                "generation_id": payload.get("generation_id"),
                "content": payload,
                "markdown": markdown,
                "summary": summary,
            },
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

    async def save_v4_report_generation(
        self,
        job_id: str,
        generation_id: str,
        payload: dict[str, Any],
        markdown: str,
        summary: dict[str, Any],
        checkpoint: dict[str, Any],
        sections: dict[str, dict[str, Any]] | None = None,
    ) -> str:
        """Atomically switch the report generation and its experiment marker."""
        response = await self._request(
            "POST",
            "/rest/v1/rpc/save_v4_report_generation",
            json={
                "p_job_id": job_id,
                "p_generation_id": generation_id,
                "p_content": payload,
                "p_markdown": markdown,
                "p_summary": summary,
                "p_checkpoint": checkpoint,
                "p_sections": _postgres_json(sections or {}),
            },
        )
        report_id = str(response.json())
        await self._request(
            "PATCH",
            f"/rest/v1/report_evidence_assets?job_id=eq.{quote(job_id)}",
            headers={"Prefer": "return=minimal"},
            json={"report_id": report_id},
        )
        return report_id

    async def resume_job_from_v4_ideas(
        self, job_id: str, expected_sha256: str, generation_id: str
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/resume_job_from_v4_ideas",
            json={
                "p_job_id": job_id,
                "p_expected_sha256": expected_sha256,
                "p_new_generation_id": generation_id,
            },
        )
        return dict(response.json())

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

    async def schedule_job_retry(
        self,
        job_id: str,
        status: JobStatus,
        retry_seconds: int,
        failure_category: str,
        safe_error: str | None = None,
    ) -> None:
        await self._request(
            "POST",
            "/rest/v1/rpc/schedule_job_retry",
            json={
                "p_job_id": job_id,
                "p_status": status.value,
                "p_retry_seconds": retry_seconds,
                "p_failure_category": failure_category,
                "p_safe_error": safe_error,
            },
        )

    async def enqueue_primary_experiment(
        self,
        job_id: str,
        user_id: str,
        idea_key: str,
        *,
        max_spend_usd: float = 90,
        llm_reservation_cny: float = 5,
        global_llm_max_cny: float = 200,
    ) -> ExperimentRecord:
        response = await self._request(
            "GET",
            f"/rest/v1/reports?job_id=eq.{quote(job_id)}&select=id&limit=1",
        )
        rows = response.json()
        if not rows:
            raise RuntimeError("Cannot enqueue an experiment before its report is saved")
        result = await self._request(
            "POST",
            "/rest/v1/rpc/enqueue_idea_experiment",
            json={
                "p_report_id": rows[0]["id"],
                "p_idea_key": idea_key,
                "p_user_id": user_id,
                "p_automatic": True,
                "p_max_spend_usd": max_spend_usd,
                "p_llm_reservation_cny": llm_reservation_cny,
                "p_global_llm_max_cny": global_llm_max_cny,
            },
        )
        return ExperimentRecord.model_validate(result.json())

    async def pending_primary_experiments(self, limit: int = 25) -> list[dict[str, str]]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/list_pending_primary_experiments",
            json={"p_limit": max(1, min(limit, 100))},
        )
        return [
            {
                "job_id": str(row["job_id"]),
                "user_id": str(row["user_id"]),
                "idea_key": str(row["idea_key"]),
            }
            for row in response.json()
        ]

    async def mark_primary_experiment_enqueued(
        self, job_id: str, experiment_id: str
    ) -> None:
        checkpoint = await self.load_pipeline_checkpoint(job_id)
        request = dict(checkpoint.get("experiment_auto_enqueue") or {})
        request.update(
            {
                "state": "enqueued",
                "experiment_id": experiment_id,
                "enqueued_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        checkpoint["experiment_auto_enqueue"] = request
        await self.save_pipeline_checkpoint(job_id, checkpoint)

    async def claim_next_experiment(
        self,
        worker_id: str,
        lease_seconds: int,
        max_concurrency: int,
        max_spend_usd: float,
        estimated_cost_per_second_usd: float = 0.000092,
        reserve_seconds: int = 3600,
    ) -> ExperimentRecord | None:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/claim_next_experiment",
            json={
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
                "p_max_concurrency": max_concurrency,
                "p_max_spend_usd": max_spend_usd,
                "p_estimated_cost_per_second_usd": max(
                    estimated_cost_per_second_usd, 0.000000001
                ),
                "p_reserve_seconds": max(60, min(reserve_seconds, 3600)),
            },
        )
        rows = response.json()
        if not rows:
            return None
        row = rows[0] if isinstance(rows, list) else rows
        return ExperimentRecord.model_validate(row)

    async def claim_next_experiment_repository_generation(
        self, worker_id: str, lease_seconds: int
    ) -> ExperimentRecord | None:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/claim_next_experiment_repository_generation",
            json={
                "p_worker_id": worker_id,
                "p_lease_seconds": max(60, lease_seconds),
            },
        )
        rows = response.json()
        if not rows:
            return None
        row = rows[0] if isinstance(rows, list) else rows
        return ExperimentRecord.model_validate(row)

    async def load_experiment(self, experiment_id: str) -> ExperimentRecord:
        response = await self._request(
            "GET",
            f"/rest/v1/idea_experiments?id=eq.{quote(experiment_id)}&select=*&limit=1",
        )
        rows = response.json()
        if not rows:
            raise RuntimeError("Experiment no longer exists")
        return ExperimentRecord.model_validate(rows[0])

    async def update_experiment(self, experiment_id: str, **values: Any) -> None:
        await self._request(
            "PATCH",
            f"/rest/v1/idea_experiments?id=eq.{quote(experiment_id)}",
            headers={"Prefer": "return=minimal"},
            json=values,
        )

    async def renew_experiment_lease(
        self, experiment_id: str, worker_id: str, lease_seconds: int
    ) -> bool:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/renew_experiment_lease",
            json={
                "p_experiment_id": experiment_id,
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
            },
        )
        return bool(response.json())

    async def save_experiment_checkpoint(
        self,
        experiment_id: str,
        checkpoint: dict[str, Any],
        *,
        worker_id: str,
        stage: str,
        progress: int,
        action_id: str | None = None,
    ) -> bool:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/save_experiment_checkpoint",
            json={
                "p_experiment_id": experiment_id,
                "p_worker_id": worker_id,
                "p_stage": stage,
                "p_progress": max(0, min(100, progress)),
                "p_checkpoint": checkpoint,
                "p_action_id": action_id,
            },
        )
        return bool(response.json())

    async def update_claimed_experiment(
        self,
        experiment_id: str,
        *,
        worker_id: str,
        action_id: str | None = None,
        pilot_specification: dict[str, Any] | None = None,
        pilot_specification_hash: str | None = None,
        pilot_compilation_required: bool | None = None,
        baseline_revision_id: str | None = None,
        current_revision_id: str | None = None,
        repair_count: int | None = None,
        latest_run_id: str | None = None,
        outcome: str | None = None,
        public_summary: dict[str, Any] | None = None,
    ) -> ExperimentRecord:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/update_claimed_experiment",
            json={
                "p_experiment_id": experiment_id,
                "p_worker_id": worker_id,
                "p_action_id": action_id,
                "p_pilot_specification": pilot_specification,
                "p_pilot_specification_hash": pilot_specification_hash,
                "p_pilot_compilation_required": pilot_compilation_required,
                "p_baseline_revision_id": baseline_revision_id,
                "p_current_revision_id": current_revision_id,
                "p_repair_count": repair_count,
                "p_latest_run_id": latest_run_id,
                "p_outcome": outcome,
                "p_public_summary": public_summary,
            },
        )
        return ExperimentRecord.model_validate(response.json())

    async def schedule_experiment_retry(
        self,
        experiment_id: str,
        worker_id: str,
        status: ExperimentStatus,
        retry_seconds: int,
        failure_category: str,
        safe_error: str | None = None,
    ) -> ExperimentRecord:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/schedule_experiment_retry",
            json={
                "p_experiment_id": experiment_id,
                "p_worker_id": worker_id,
                "p_status": status.value,
                "p_retry_seconds": retry_seconds,
                "p_failure_category": failure_category,
                "p_safe_error": safe_error,
            },
        )
        return ExperimentRecord.model_validate(response.json())

    async def finish_experiment(
        self,
        experiment_id: str,
        worker_id: str,
        *,
        status: ExperimentStatus,
        outcome: str,
        public_summary: dict[str, Any],
    ) -> ExperimentRecord:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/finish_experiment",
            json={
                "p_experiment_id": experiment_id,
                "p_worker_id": worker_id,
                "p_status": status.value,
                "p_outcome": outcome,
                "p_public_summary": public_summary,
            },
        )
        return ExperimentRecord.model_validate(response.json())

    async def load_experiment_runtime(self, experiment_id: str) -> dict[str, Any] | None:
        response = await self._request(
            "GET",
            f"/rest/v1/experiment_runtime?experiment_id=eq.{quote(experiment_id)}&select=*&limit=1",
        )
        rows = response.json()
        return dict(rows[0]) if rows else None

    async def load_validation_runtime(self, action_id: str) -> dict[str, Any] | None:
        response = await self._request(
            "GET",
            "/rest/v1/experiment_validation_runtime"
            f"?action_id=eq.{quote(action_id)}&select=*&limit=1",
        )
        rows = response.json()
        return dict(rows[0]) if rows else None

    async def reserve_claimed_validation_runtime(
        self,
        experiment_id: str,
        *,
        action_id: str,
        worker_id: str,
        run_id: str,
        max_spend_usd: float,
        max_concurrency: int = 1,
        estimated_cost_per_second_usd: float = 0.000092,
        reserve_seconds: int = 3600,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/reserve_claimed_validation_runtime",
            json={
                "p_experiment_id": experiment_id,
                "p_action_id": action_id,
                "p_worker_id": worker_id,
                "p_run_id": run_id,
                "p_max_spend_usd": max(0, max_spend_usd),
                "p_max_concurrency": max(1, max_concurrency),
                "p_estimated_cost_per_second_usd": max(
                    estimated_cost_per_second_usd, 0.000000001
                ),
                "p_reserve_seconds": max(60, min(reserve_seconds, 3600)),
            },
        )
        return dict(response.json())

    async def attach_claimed_validation_runtime(
        self,
        experiment_id: str,
        *,
        action_id: str,
        worker_id: str,
        sandbox_id: str,
        destroy_after: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/attach_claimed_validation_runtime",
            json={
                "p_experiment_id": experiment_id,
                "p_action_id": action_id,
                "p_worker_id": worker_id,
                "p_sandbox_id": sandbox_id,
                "p_destroy_after": destroy_after,
                "p_metadata": metadata or {},
            },
        )
        return dict(response.json())

    async def finish_claimed_validation_runtime(
        self,
        experiment_id: str,
        *,
        action_id: str,
        worker_id: str,
        sandbox_id: str | None,
        destroyed: bool,
        retry_seconds: int = 30,
        safe_error: str | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/finish_claimed_validation_runtime",
            json={
                "p_experiment_id": experiment_id,
                "p_action_id": action_id,
                "p_worker_id": worker_id,
                "p_sandbox_id": sandbox_id,
                "p_destroyed": destroyed,
                "p_retry_seconds": max(1, retry_seconds),
                "p_safe_error": safe_error,
            },
        )
        return dict(response.json())

    async def claim_expired_validation_runtimes(
        self, limit: int = 10
    ) -> list[dict[str, Any]]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/claim_expired_validation_runtime",
            json={"p_limit": max(1, min(limit, 100))},
        )
        rows = response.json()
        return [dict(row) for row in rows] if isinstance(rows, list) else []

    async def finish_validation_runtime_lifecycle(
        self,
        action_id: str,
        *,
        claim_token: str,
        destroyed: bool,
        retry_seconds: int = 30,
        safe_error: str | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/finish_validation_runtime_lifecycle",
            json={
                "p_action_id": action_id,
                "p_claim_token": claim_token,
                "p_destroyed": destroyed,
                "p_retry_seconds": max(1, retry_seconds),
                "p_safe_error": safe_error,
            },
        )
        return dict(response.json())

    async def save_experiment_runtime(
        self, experiment_id: str, **values: Any
    ) -> None:
        await self._request(
            "POST",
            "/rest/v1/experiment_runtime?on_conflict=experiment_id",
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"experiment_id": experiment_id, **values},
        )

    async def save_claimed_experiment_runtime(
        self,
        experiment_id: str,
        *,
        worker_id: str,
        action_id: str | None = None,
        state: str,
        sandbox_id: str | None = None,
        paused_at: str | None = None,
        clear_paused_at: bool = False,
        destroy_after: str | None = None,
        last_heartbeat_at: str | None = None,
        metadata: dict[str, Any] | None = None,
        estimated_cost_per_second_usd: float = 0.000092,
        reserve_seconds: int = 3600,
        max_spend_usd: float = 90,
        max_concurrency: int = 1,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/save_claimed_experiment_runtime",
            json={
                "p_experiment_id": experiment_id,
                "p_worker_id": worker_id,
                "p_action_id": action_id,
                "p_state": state,
                "p_sandbox_id": sandbox_id,
                "p_paused_at": paused_at,
                "p_clear_paused_at": clear_paused_at,
                "p_destroy_after": destroy_after,
                "p_last_heartbeat_at": last_heartbeat_at,
                "p_metadata": metadata,
                "p_estimated_cost_per_second_usd": max(
                    estimated_cost_per_second_usd, 0.000000001
                ),
                "p_reserve_seconds": max(60, min(reserve_seconds, 3600)),
                "p_max_spend_usd": max(0, max_spend_usd),
                "p_max_concurrency": max(1, max_concurrency),
            },
        )
        return dict(response.json())

    async def finish_experiment_runtime_lifecycle(
        self,
        experiment_id: str,
        *,
        claim_token: str,
        lifecycle_action: str,
        state: str,
        sandbox_id: str | None = None,
        paused_at: str | None = None,
        destroy_after: str | None = None,
        last_heartbeat_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/finish_experiment_runtime_lifecycle",
            json={
                "p_experiment_id": experiment_id,
                "p_claim_token": claim_token,
                "p_lifecycle_action": lifecycle_action,
                "p_state": state,
                "p_sandbox_id": sandbox_id,
                "p_paused_at": paused_at,
                "p_destroy_after": destroy_after,
                "p_last_heartbeat_at": last_heartbeat_at,
                "p_metadata": metadata,
            },
        )
        return dict(response.json())

    async def schedule_claimed_runtime_cleanup(
        self,
        experiment_id: str,
        *,
        worker_id: str,
        action_id: str | None = None,
        sandbox_id: str,
        retry_seconds: int = 30,
        safe_error: str | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/schedule_claimed_runtime_cleanup",
            json={
                "p_experiment_id": experiment_id,
                "p_worker_id": worker_id,
                "p_action_id": action_id,
                "p_sandbox_id": sandbox_id,
                "p_retry_seconds": max(1, retry_seconds),
                "p_safe_error": safe_error,
            },
        )
        return dict(response.json())

    async def mark_experiment_runtime_tainted(
        self,
        experiment_id: str,
        *,
        sandbox_id: str,
        action_id: str | None = None,
        safe_error: str | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/mark_experiment_runtime_tainted",
            json={
                "p_experiment_id": experiment_id,
                "p_sandbox_id": sandbox_id,
                "p_action_id": action_id,
                "p_safe_error": safe_error,
            },
        )
        return dict(response.json())

    async def create_experiment_revision(
        self,
        experiment_id: str,
        *,
        parent_revision_id: str | None,
        actor: str,
        git_commit: str,
        tree_hash: str,
        bundle_storage_path: str | None,
        summary: dict[str, Any],
        immutable: bool,
        worker_id: str,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/create_experiment_revision",
            json={
                "p_experiment_id": experiment_id,
                "p_parent_revision_id": parent_revision_id,
                "p_actor": actor,
                "p_git_commit": git_commit,
                "p_tree_hash": tree_hash,
                "p_bundle_storage_path": bundle_storage_path,
                "p_summary": summary,
                "p_immutable": immutable,
                "p_worker_id": worker_id,
                "p_action_id": action_id,
            },
        )
        return dict(response.json())

    async def create_experiment_run(
        self,
        experiment_id: str,
        *,
        revision_id: str | None,
        trigger_kind: str,
        reuse_running: bool = False,
        worker_id: str,
        action_id: str | None = None,
        max_active_seconds: int = 3600,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/create_experiment_run",
            json={
                "p_experiment_id": experiment_id,
                "p_revision_id": revision_id,
                "p_trigger_kind": trigger_kind,
                "p_reuse_running": reuse_running,
                "p_worker_id": worker_id,
                "p_action_id": action_id,
                "p_max_active_seconds": min(3600, max(1, max_active_seconds)),
            },
        )
        return dict(response.json())

    async def assert_experiment_run_within_deadline(
        self,
        run_id: str,
        *,
        worker_id: str,
        action_id: str | None = None,
    ) -> int:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/assert_experiment_run_within_deadline",
            json={
                "p_run_id": run_id,
                "p_worker_id": worker_id,
                "p_action_id": action_id,
            },
        )
        return int(response.json())

    async def increment_experiment_costs(
        self,
        experiment_id: str,
        *,
        llm_cost_cny: float = 0,
        e2b_seconds: int = 0,
        e2b_cost_usd: float = 0,
        worker_id: str,
        action_id: str | None = None,
        job_id: str | None = None,
        usage: ProviderUsage | None = None,
    ) -> ExperimentRecord:
        usage_metadata = dict(usage.metadata) if usage else {}
        response = await self._request(
            "POST",
            "/rest/v1/rpc/increment_experiment_costs",
            json={
                "p_experiment_id": experiment_id,
                "p_llm_cost_cny": max(0, llm_cost_cny),
                "p_e2b_seconds": max(0, e2b_seconds),
                "p_e2b_cost_usd": max(0, e2b_cost_usd),
                "p_worker_id": worker_id,
                "p_action_id": action_id,
                "p_job_id": job_id,
                "p_provider": usage.provider if usage else None,
                "p_model": usage.model if usage else None,
                "p_input_tokens": usage.input_tokens if usage else 0,
                "p_output_tokens": usage.output_tokens if usage else 0,
                "p_requests": usage.requests if usage else 0,
                "p_usage_metadata": usage_metadata,
                "p_usage_id": usage_metadata.get("experiment_usage_id"),
            },
        )
        return ExperimentRecord.model_validate(response.json())

    async def sync_experiment_run_costs(
        self, experiment_id: str
    ) -> ExperimentRecord:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/sync_experiment_run_costs",
            json={"p_experiment_id": experiment_id},
        )
        return ExperimentRecord.model_validate(response.json())

    async def authorize_experiment_llm_call(
        self,
        experiment_id: str,
        *,
        worker_id: str,
        action_id: str | None = None,
        usage_id: str,
        max_call_cny: float,
    ) -> ExperimentRecord:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/authorize_experiment_llm_call",
            json={
                "p_experiment_id": experiment_id,
                "p_worker_id": worker_id,
                "p_action_id": action_id,
                "p_usage_id": usage_id,
                "p_max_call_cny": max_call_cny,
            },
        )
        return ExperimentRecord.model_validate(response.json())

    async def settle_experiment_llm_reservation(
        self,
        experiment_id: str,
        *,
        worker_id: str,
        action_id: str | None = None,
        reason: str = "provider_usage_unavailable",
        usage_id: str,
    ) -> ExperimentRecord:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/settle_experiment_llm_reservation",
            json={
                "p_experiment_id": experiment_id,
                "p_worker_id": worker_id,
                "p_action_id": action_id,
                "p_reason": reason[:200],
                "p_usage_id": usage_id,
            },
        )
        return ExperimentRecord.model_validate(response.json())

    async def replace_sandbox_inference_tokens(
        self,
        experiment_id: str,
        run_id: str,
        *,
        worker_id: str,
        action_id: str | None,
        specification_hash: str,
        tokens: list[dict[str, Any]],
        expires_at: str,
    ) -> list[str]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/replace_sandbox_inference_tokens",
            json={
                "p_experiment_id": experiment_id,
                "p_run_id": run_id,
                "p_worker_id": worker_id,
                "p_action_id": action_id,
                "p_specification_hash": specification_hash,
                "p_tokens": tokens,
                "p_expires_at": expires_at,
            },
        )
        value = response.json()
        return [str(item) for item in value] if isinstance(value, list) else []

    async def claim_next_sandbox_inference_request(
        self,
        worker_id: str,
        lease_seconds: int,
        *,
        max_call_cny: float,
        run_max_cny: float,
    ) -> dict[str, Any] | None:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/claim_next_sandbox_inference_request",
            json={
                "p_worker_id": worker_id,
                "p_lease_seconds": max(60, lease_seconds),
                "p_max_call_cny": max(0, max_call_cny),
                "p_run_max_cny": max(0, min(run_max_cny, 5)),
            },
        )
        rows = response.json()
        if not rows:
            return None
        return dict(rows[0] if isinstance(rows, list) else rows)

    async def renew_sandbox_inference_lease(
        self, request_id: str, worker_id: str, lease_seconds: int
    ) -> bool:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/renew_sandbox_inference_lease",
            json={
                "p_request_id": request_id,
                "p_worker_id": worker_id,
                "p_lease_seconds": max(60, lease_seconds),
            },
        )
        return bool(response.json())

    async def mark_sandbox_inference_provider_started(
        self, request_id: str, worker_id: str
    ) -> bool:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/mark_sandbox_inference_provider_started",
            json={"p_request_id": request_id, "p_worker_id": worker_id},
        )
        return bool(response.json())

    async def schedule_sandbox_inference_retry(
        self, request_id: str, worker_id: str, retry_seconds: int = 30
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/schedule_sandbox_inference_retry",
            json={
                "p_request_id": request_id,
                "p_worker_id": worker_id,
                "p_retry_seconds": max(1, min(retry_seconds, 600)),
            },
        )
        return dict(response.json())

    async def finish_sandbox_inference_request(
        self,
        request_id: str,
        worker_id: str,
        *,
        status: str,
        response_payload: dict[str, Any] | None = None,
        response_sha256: str | None = None,
        provider: str = "deepseek",
        model: str = "deepseek-v4-flash",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_cny: float | None = None,
        settlement_kind: str = "exact_usage",
        public_error_code: str | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/finish_sandbox_inference_request",
            json={
                "p_request_id": request_id,
                "p_worker_id": worker_id,
                "p_status": status,
                "p_response": response_payload,
                "p_response_sha256": response_sha256,
                "p_provider": provider,
                "p_model": model,
                "p_input_tokens": max(0, input_tokens),
                "p_output_tokens": max(0, output_tokens),
                "p_cost_cny": cost_cny,
                "p_settlement_kind": settlement_kind,
                "p_public_error_code": public_error_code,
            },
        )
        return dict(response.json())

    async def claim_expired_experiment_runtimes(
        self, limit: int = 10
    ) -> list[dict[str, Any]]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/claim_expired_experiment_runtime",
            json={"p_limit": max(1, min(limit, 100))},
        )
        rows = response.json()
        return [dict(row) for row in rows] if isinstance(rows, list) else []

    async def claim_idle_experiment_runtimes(
        self, idle_seconds: int, limit: int = 10
    ) -> list[dict[str, Any]]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/claim_idle_experiment_runtime",
            json={
                "p_idle_seconds": max(60, idle_seconds),
                "p_limit": max(1, min(limit, 100)),
            },
        )
        rows = response.json()
        return [dict(row) for row in rows] if isinstance(rows, list) else []

    async def get_experiment_revision(
        self, experiment_id: str, revision_id: str
    ) -> dict[str, Any]:
        response = await self._request(
            "GET",
            "/rest/v1/experiment_revisions"
            f"?id=eq.{quote(revision_id)}&experiment_id=eq.{quote(experiment_id)}"
            "&select=*&limit=1",
        )
        rows = response.json()
        if not rows:
            raise RuntimeError("Experiment revision does not exist")
        return dict(rows[0])

    async def download_experiment_storage(self, storage_path: str) -> bytes:
        encoded_path = "/".join(
            quote(part, safe="") for part in storage_path.split("/") if part
        )
        if not encoded_path:
            raise ValueError("Experiment artifact storage path is empty")
        response = await self._request(
            "GET", f"/storage/v1/object/experiment-artifacts/{encoded_path}"
        )
        return response.content

    async def load_experiment_chat_attachments(
        self, experiment_id: str, user_id: str, attachment_ids: list[str]
    ) -> list[dict[str, Any]]:
        unique_ids = list(dict.fromkeys(attachment_ids))
        if not unique_ids:
            return []
        if len(unique_ids) > 4:
            raise ValueError("Too many experiment chat attachments")
        encoded_ids = ",".join(quote(item, safe="") for item in unique_ids)
        response = await self._request(
            "GET",
            "/rest/v1/experiment_chat_attachments"
            f"?id=in.({encoded_ids})"
            f"&experiment_id=eq.{quote(experiment_id)}"
            f"&user_id=eq.{quote(user_id)}"
            "&status=eq.bound&select=id,storage_path,file_name,mime_type,byte_size,sha256,width,height,created_at",
        )
        rows = response.json()
        if not isinstance(rows, list) or len(rows) != len(unique_ids):
            raise ValueError("Experiment chat attachment is unavailable")
        by_id = {str(row.get("id") or ""): dict(row) for row in rows}
        return [by_id[item] for item in unique_ids if item in by_id]

    async def download_experiment_chat_attachment(self, storage_path: str) -> bytes:
        encoded_path = "/".join(
            quote(part, safe="") for part in storage_path.split("/") if part
        )
        if not encoded_path:
            raise ValueError("Experiment chat attachment storage path is empty")
        response = await self._request(
            "GET", f"/storage/v1/object/experiment-chat-attachments/{encoded_path}"
        )
        return response.content

    async def update_experiment_run(self, run_id: str, **values: Any) -> None:
        await self._request(
            "PATCH",
            f"/rest/v1/experiment_runs?id=eq.{quote(run_id)}",
            headers={"Prefer": "return=minimal"},
            json=values,
        )

    async def finalize_experiment_run(
        self,
        run_id: str,
        *,
        status: str,
        outcome: str,
        commands: dict[str, Any] | list[Any] | None = None,
        metrics: dict[str, Any] | None = None,
        evaluation: dict[str, Any] | None = None,
        safe_error: str | None = None,
        e2b_seconds: int = 0,
        e2b_cost_usd: float = 0,
        llm_cost_cny: float = 0,
        worker_id: str,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/finalize_experiment_run",
            json={
                "p_run_id": run_id,
                "p_status": status,
                "p_outcome": outcome,
                "p_commands": commands or {},
                "p_metrics": metrics or {},
                "p_evaluation": evaluation or {},
                "p_safe_error": safe_error,
                "p_e2b_seconds": max(0, e2b_seconds),
                "p_e2b_cost_usd": max(0, e2b_cost_usd),
                "p_llm_cost_cny": max(0, llm_cost_cny),
                "p_worker_id": worker_id,
                "p_action_id": action_id,
            },
        )
        return dict(response.json())

    async def upload_experiment_artifact(
        self,
        *,
        experiment: ExperimentRecord,
        kind: str,
        file_name: str,
        content: bytes,
        run_id: str | None = None,
        revision_id: str | None = None,
        public_safe: bool = False,
        metadata: dict[str, Any] | None = None,
        mime_type: str = "application/octet-stream",
        worker_id: str,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        digest = hashlib.sha256(content).hexdigest()
        scope = revision_id or run_id or "workspace"
        safe_name = file_name.replace("/", "_").replace("\\", "_")
        storage_path = f"{experiment.user_id}/{experiment.id}/{scope}/{digest[:12]}-{safe_name}"
        encoded_path = "/".join(quote(part, safe="") for part in storage_path.split("/"))
        await self._request(
            "POST",
            f"/storage/v1/object/experiment-artifacts/{encoded_path}",
            headers={"Content-Type": mime_type, "x-upsert": "true"},
            content=content,
        )
        try:
            response = await self._request(
                "POST",
                "/rest/v1/rpc/register_claimed_experiment_artifact",
                json={
                    "p_experiment_id": experiment.id,
                    "p_worker_id": worker_id,
                    "p_action_id": action_id,
                    "p_run_id": run_id,
                    "p_revision_id": revision_id,
                    "p_kind": kind,
                    "p_storage_path": storage_path,
                    "p_file_name": file_name,
                    "p_mime_type": mime_type,
                    "p_byte_size": len(content),
                    "p_sha256": digest,
                    "p_public_safe": public_safe,
                    "p_metadata": metadata or {},
                },
            )
        except Exception:
            # A lost lease can leave an upload without a corresponding DB row.
            # Query first: an ambiguous RPC response may have committed, in
            # which case deleting the deterministic object would corrupt a
            # valid artifact. Only an observed orphan is removed best-effort.
            try:
                lookup = await self._request(
                    "GET",
                    "/rest/v1/experiment_artifacts"
                    f"?storage_path=eq.{quote(storage_path, safe='')}&select=id&limit=1",
                )
                if not lookup.json():
                    await self._request(
                        "DELETE",
                        "/storage/v1/object/experiment-artifacts",
                        json={"prefixes": [storage_path]},
                    )
            except Exception as cleanup_error:
                LOGGER.warning(
                    "Could not clean unregistered experiment artifact %s: %s",
                    storage_path,
                    redact(str(cleanup_error)),
                )
            raise
        return dict(response.json())

    async def claim_next_experiment_action(
        self,
        worker_id: str,
        lease_seconds: int,
        max_spend_usd: float = 90,
        max_concurrency: int = 1,
        estimated_cost_per_second_usd: float = 0.000092,
        reserve_seconds: int = 3600,
    ) -> dict[str, Any] | None:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/claim_next_experiment_action",
            json={
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
                "p_max_spend_usd": max(0, max_spend_usd),
                "p_max_concurrency": max(1, max_concurrency),
                "p_estimated_cost_per_second_usd": max(
                    estimated_cost_per_second_usd, 0.000000001
                ),
                "p_reserve_seconds": max(60, min(reserve_seconds, 3600)),
            },
        )
        rows = response.json()
        if not rows:
            return None
        return dict(rows[0] if isinstance(rows, list) else rows)

    async def claim_next_experiment_answer_action(
        self, worker_id: str, lease_seconds: int
    ) -> dict[str, Any] | None:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/claim_next_experiment_answer_action",
            json={
                "p_worker_id": worker_id,
                "p_lease_seconds": max(60, lease_seconds),
            },
        )
        rows = response.json()
        if not rows:
            return None
        return dict(rows[0] if isinstance(rows, list) else rows)

    async def prepare_queued_experiment_mutations(self) -> int:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/prepare_queued_experiment_mutations",
            json={},
        )
        return int(response.json() or 0)

    async def enqueue_assistant_followup_validation(
        self,
        experiment_id: str,
        user_id: str,
        revision_id: str,
        source_action_id: str,
        *,
        llm_reservation_cny: float,
        experiment_llm_max_cny: float,
        global_llm_max_cny: float,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/enqueue_assistant_followup_validation",
            json={
                "p_experiment_id": experiment_id,
                "p_user_id": user_id,
                "p_base_revision_id": revision_id,
                "p_source_action_id": source_action_id,
                "p_llm_reservation_cny": llm_reservation_cny,
                "p_experiment_llm_max_cny": experiment_llm_max_cny,
                "p_global_llm_max_cny": global_llm_max_cny,
            },
        )
        return dict(response.json())

    async def claim_next_experiment_cleanup(
        self, worker_id: str, lease_seconds: int
    ) -> ExperimentRecord | None:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/claim_next_experiment_cleanup",
            json={
                "p_worker_id": worker_id,
                "p_lease_seconds": max(60, lease_seconds),
            },
        )
        rows = response.json()
        if not rows:
            return None
        row = rows[0] if isinstance(rows, list) else rows
        return ExperimentRecord.model_validate(row)

    async def renew_experiment_action_lease(
        self, action_id: str, worker_id: str, lease_seconds: int
    ) -> bool:
        response = await self._request(
            "POST",
            "/rest/v1/rpc/renew_experiment_action_lease",
            json={
                "p_action_id": action_id,
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
            },
        )
        return bool(response.json())

    async def update_experiment_action_progress(
        self, action_id: str, worker_id: str, response: dict[str, Any]
    ) -> bool:
        result = await self._request(
            "POST",
            "/rest/v1/rpc/save_experiment_action_progress",
            json={
                "p_action_id": action_id,
                "p_worker_id": worker_id,
                "p_response": response,
            },
        )
        return bool(result.json())

    async def finish_experiment_action(
        self,
        action_id: str,
        worker_id: str,
        *,
        success: bool,
        response: dict[str, Any] | None = None,
        result_revision_id: str | None = None,
        retry_seconds: int = 30,
        safe_error: str | None = None,
    ) -> dict[str, Any]:
        result = await self._request(
            "POST",
            "/rest/v1/rpc/finish_experiment_action",
            json={
                "p_action_id": action_id,
                "p_worker_id": worker_id,
                "p_success": success,
                "p_response": response or {},
                "p_result_revision_id": result_revision_id,
                "p_retry_seconds": retry_seconds,
                "p_safe_error": safe_error,
            },
        )
        return dict(result.json())

    async def delete_experiment(self, experiment_id: str) -> None:
        await self._request(
            "DELETE",
            f"/rest/v1/idea_experiments?id=eq.{quote(experiment_id)}",
            headers={"Prefer": "return=minimal"},
        )
