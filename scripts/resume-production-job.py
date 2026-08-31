#!/usr/bin/env python3
"""Requeue a failed production job while preserving its durable checkpoint."""

from __future__ import annotations

import argparse
import asyncio
import json
from urllib.parse import quote

from paper_research.clients.supabase import SupabaseRepository
from paper_research.config import Settings


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    args = parser.parse_args()
    job_id = quote(args.job_id, safe="")
    settings = Settings()
    repository = SupabaseRepository(
        settings.SUPABASE_URL or "",
        Settings.reveal(settings.SUPABASE_SERVICE_ROLE_KEY) or "",
    )
    try:
        rows = (
            await repository._request(
                "GET",
                f"/rest/v1/jobs?id=eq.{job_id}&select=id,status,checkpoint",
            )
        ).json()
        if not rows:
            raise RuntimeError("Job not found")
        job = rows[0]
        if job["status"] not in {"failed", "budget_blocked"}:
            raise RuntimeError(f"Job cannot be resumed from status {job['status']}")
        checkpoint = job.get("checkpoint") or {}
        await repository._request(
            "PATCH",
            f"/rest/v1/jobs?id=eq.{job_id}",
            headers={"Prefer": "return=minimal"},
            json={
                "status": "queued",
                "stage": "queued",
                "error": None,
                "worker_id": None,
                "lease_expires_at": None,
                "cancellation_requested": False,
                "completed_at": None,
            },
        )
        await repository._request(
            "POST",
            "/rest/v1/job_events",
            headers={"Prefer": "return=minimal"},
            json={
                "job_id": args.job_id,
                "kind": "resumed",
                "message": "Job requeued from its durable checkpoint",
                "data": {"checkpoint_sections": sorted(checkpoint)},
            },
        )
        print(
            json.dumps(
                {
                    "job_id": args.job_id,
                    "status": "queued",
                    "checkpoint_preserved": bool(checkpoint),
                }
            )
        )
    finally:
        await repository.close()


if __name__ == "__main__":
    asyncio.run(main())
