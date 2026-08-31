#!/usr/bin/env python3
"""Print a compact, non-sensitive production job snapshot."""

from __future__ import annotations

import argparse
import asyncio
import json
from urllib.parse import quote

from paper_research.clients.supabase import SupabaseRepository
from paper_research.config import Settings


async def count_rows(repository: SupabaseRepository, path: str) -> int:
    response = await repository._request(
        "GET",
        path,
        headers={"Prefer": "count=exact", "Range": "0-0"},
    )
    content_range = response.headers.get("content-range", "0-0/0")
    return int(content_range.rsplit("/", 1)[-1])


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
        jobs = (
            await repository._request(
                "GET",
                f"/rest/v1/jobs?id=eq.{job_id}"
                "&select=id,status,stage,progress,current_round,error,updated_at",
            )
        ).json()
        if not jobs:
            raise RuntimeError("Job not found")
        events = (
            await repository._request(
                "GET",
                f"/rest/v1/job_events?job_id=eq.{job_id}"
                "&select=kind,message,data,created_at&order=created_at.desc&limit=1",
            )
        ).json()
        reports = (
            await repository._request(
                "GET",
                f"/rest/v1/reports?job_id=eq.{job_id}&select=id,summary",
            )
        ).json()
        report = reports[0] if reports else None
        summary = report.get("summary") if report else None
        presentation = (summary or {}).get("presentation") or {}
        assets = (
            await repository._request(
                "GET",
                f"/rest/v1/report_evidence_assets?job_id=eq.{job_id}&select=id",
            )
        ).json()
        asset_filter = ",".join(quote(str(asset["id"]), safe="") for asset in assets)
        result = {
            **jobs[0],
            "latest_event": events[0] if events else None,
            "candidate_papers": await count_rows(
                repository,
                f"/rest/v1/candidate_papers?job_id=eq.{job_id}&select=id",
            ),
            "evidence_assets": len(assets),
            "report_id": report.get("id") if report else None,
            "summary_bytes": (
                len(json.dumps(summary, ensure_ascii=False).encode("utf-8"))
                if summary
                else 0
            ),
            "passed_ideas": len(presentation.get("ideas") or []),
            "promising_ideas": len(presentation.get("promising_ideas") or []),
            "report_sections": (
                await count_rows(
                    repository,
                    f"/rest/v1/report_sections?report_id=eq.{quote(str(report['id']), safe='')}&select=id",
                )
                if report
                else 0
            ),
            "preview_pages": (
                await count_rows(
                    repository,
                    f"/rest/v1/report_evidence_previews?asset_id=in.({asset_filter})&select=id",
                )
                if asset_filter
                else 0
            ),
        }
        print(json.dumps(result, ensure_ascii=False, default=str))
    finally:
        await repository.close()


if __name__ == "__main__":
    asyncio.run(main())
