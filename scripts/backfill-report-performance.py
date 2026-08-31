#!/usr/bin/env python3
"""Backfill compact report sections and missing evidence previews for a V4 report."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from urllib.parse import quote

from paper_research.clients.supabase import SupabaseRepository
from paper_research.config import Settings
from paper_research.models import AnalysisReport
from paper_research.pipeline import report_section_payloads, report_summary


async def run(job_id: str | None) -> None:
    settings = Settings()
    settings.require_worker_secrets()
    repository = SupabaseRepository(
        settings.SUPABASE_URL or "",
        Settings.reveal(settings.SUPABASE_SERVICE_ROLE_KEY) or "",
    )
    try:
        query = "/rest/v1/reports?select=id,job_id,content,markdown,created_at&order=created_at.desc&limit=20"
        if job_id:
            query = (
                "/rest/v1/reports?select=id,job_id,content,markdown,created_at"
                f"&job_id=eq.{quote(job_id)}&limit=1"
            )
        rows = (await repository._request("GET", query)).json()
        row = next(
            (
                item
                for item in rows
                if (item.get("content") or {}).get("presentation", {}).get("version") == 4
            ),
            None,
        )
        if not row:
            raise RuntimeError("No V4 report matched the requested job")
        report = AnalysisReport.model_validate(row["content"])
        summary = report_summary(report)
        sections = report_section_payloads(report)
        report_id = await repository.save_report(
            str(row["job_id"]),
            report.model_dump(mode="json"),
            str(row.get("markdown") or ""),
            summary,
            sections,
        )
        artifact_root = settings.ARTIFACT_ROOT.resolve()
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="report-preview-backfill-", dir=artifact_root
        ) as directory:
            generated = await repository.generate_evidence_previews(
                str(row["job_id"]), Path(directory), concurrency=2
            )
        print(
            json.dumps(
                {
                    "job_id": row["job_id"],
                    "report_id": report_id,
                    "summary_bytes": len(
                        json.dumps(summary, ensure_ascii=False).encode("utf-8")
                    ),
                    "sections": sorted(sections),
                    "new_preview_pages": generated,
                },
                ensure_ascii=False,
            )
        )
    finally:
        await repository.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id")
    args = parser.parse_args()
    asyncio.run(run(args.job_id))


if __name__ == "__main__":
    main()
