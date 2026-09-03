from __future__ import annotations

import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .benchmark_metrics import atomic_write_json, load_json_checkpoint
from .clients.supabase import SupabaseRepository
from .config import Settings

STATE_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def backfill_benchmark_evidence_previews(
    settings: Settings,
    *,
    benchmark_run_id: str,
    concurrency: int = 2,
    resume: bool = False,
    output: Path | None = None,
    repository: SupabaseRepository | None = None,
) -> int:
    """Idempotently render missing cited-page previews for a benchmark run."""
    try:
        normalized_run_id = str(uuid.UUID(benchmark_run_id))
    except ValueError as error:
        raise ValueError("--benchmark-run must be a UUID") from error
    if not 1 <= concurrency <= 4:
        raise ValueError("--concurrency must be between 1 and 4")

    supabase_url = ""
    service_key = ""
    if repository is None:
        supabase_url = settings.SUPABASE_URL
        service_key = Settings.reveal(settings.SUPABASE_SERVICE_ROLE_KEY)
        if not supabase_url or not service_key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required"
            )

    state_dir = (output or settings.ARTIFACT_ROOT / "evidence-preview-backfill") / normalized_run_id
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "run-state.json"
    existing = load_json_checkpoint(state_path) if resume else None
    if existing is not None and (
        not isinstance(existing, dict)
        or existing.get("schema_version") != STATE_SCHEMA_VERSION
        or existing.get("benchmark_run_id") != normalized_run_id
    ):
        raise ValueError("Evidence preview checkpoint does not match this benchmark run")
    state: dict[str, Any] = existing or {
        "schema_version": STATE_SCHEMA_VERSION,
        "benchmark_run_id": normalized_run_id,
        "status": "running",
        "created_at": _now(),
        "updated_at": _now(),
        "jobs": {},
    }

    owns_repository = repository is None
    client = repository or SupabaseRepository(supabase_url or "", service_key or "")
    try:
        jobs = await client.list_benchmark_report_jobs(normalized_run_id)
        if not jobs:
            raise ValueError("No jobs were found for this benchmark run")
        unavailable = [
            str(item.get("id") or "")
            for item in jobs
            if item.get("status") != "completed" or not item.get("report")
        ]
        if unavailable:
            raise ValueError(
                "Every benchmark job must be completed with a report before preview backfill: "
                + ", ".join(unavailable)
            )

        failures = 0
        for item in jobs:
            job_id = str(item["id"])
            record = state["jobs"].setdefault(job_id, {})
            record.update(
                {
                    "paper_id": item.get("benchmark_paper_id"),
                    "case_id": item.get("benchmark_case_id"),
                    "status": "running",
                    "started_at": record.get("started_at") or _now(),
                    "updated_at": _now(),
                }
            )
            state["status"] = "running"
            state["updated_at"] = _now()
            atomic_write_json(state_path, state)
            try:
                with tempfile.TemporaryDirectory(
                    prefix=f"{job_id[:8]}-", dir=state_dir
                ) as workspace:
                    created = await client.generate_evidence_previews(
                        job_id, Path(workspace), concurrency=concurrency
                    )
                record.update(
                    {
                        "status": "completed",
                        "new_previews": int(record.get("new_previews") or 0) + created,
                        "completed_at": _now(),
                        "updated_at": _now(),
                    }
                )
                await client.add_event(
                    job_id,
                    "evidence_previews",
                    "Citation page previews are ready",
                    {"count": created, "backfill": True},
                )
            except Exception as error:
                failures += 1
                record.update(
                    {
                        "status": "recovering",
                        "safe_error": type(error).__name__,
                        "updated_at": _now(),
                    }
                )
            finally:
                state["updated_at"] = _now()
                atomic_write_json(state_path, state)

        state["status"] = "completed" if failures == 0 else "recovering"
        state["completed_at"] = _now() if failures == 0 else None
        state["updated_at"] = _now()
        atomic_write_json(state_path, state)
        return 0 if failures == 0 else 1
    finally:
        if owns_repository:
            await client.close()
