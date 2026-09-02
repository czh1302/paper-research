#!/usr/bin/env python3
"""Submit the single approved PDF to the production worker without exposing secrets."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from urllib.parse import quote

from paper_research.clients.supabase import SupabaseRepository
from paper_research.config import Settings

ACTIVE_STATUSES = "queued,parsing,problem_ready,searching,analyzing,rendering"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()
    pdf_path = args.pdf.resolve()
    content = pdf_path.read_bytes()
    if pdf_path.suffix.lower() != ".pdf" or not content.startswith(b"%PDF"):
        raise SystemExit("The smoke input must be a PDF")
    if len(content) > 50 * 1024 * 1024:
        raise SystemExit("The smoke input exceeds 50 MB")

    settings = Settings()
    repository = SupabaseRepository(
        settings.SUPABASE_URL or "",
        Settings.reveal(settings.SUPABASE_SERVICE_ROLE_KEY) or "",
    )
    upload_id = str(uuid.uuid4())
    storage_path: str | None = None
    try:
        active_response = await repository._request(
            "GET",
            f"/rest/v1/jobs?status=in.({ACTIVE_STATUSES})&select=id&limit=1",
        )
        if active_response.json():
            raise RuntimeError("A production job is already active")
        spend = await repository.monthly_spend_cny()
        if (
            settings.BUDGET_GUARD_CNY > 0
            and spend >= settings.BUDGET_GUARD_CNY
        ):
            raise RuntimeError("The production budget guard is active")

        admins = (
            await repository._request(
                "GET", "/rest/v1/admin_users?select=user_id&limit=1"
            )
        ).json()
        if not admins:
            raise RuntimeError("No administrator account is configured")
        user_id = str(admins[0]["user_id"])
        safe_name = pdf_path.name.replace(" ", "_")
        storage_path = f"{user_id}/{upload_id}/{safe_name}"
        encoded_path = "/".join(quote(part, safe="") for part in storage_path.split("/"))
        await repository._request(
            "POST",
            f"/storage/v1/object/papers/{encoded_path}",
            headers={"Content-Type": "application/pdf", "x-upsert": "false"},
            content=content,
        )
        await repository._request(
            "POST",
            "/rest/v1/uploads",
            headers={"Prefer": "return=minimal"},
            json={
                "id": upload_id,
                "user_id": user_id,
                "storage_path": storage_path,
                "original_name": pdf_path.name,
                "size_bytes": len(content),
                "mime_type": "application/pdf",
                "sha256": hashlib.sha256(content).hexdigest(),
                "status": "uploaded",
            },
        )
        job_response = await repository._request(
            "POST",
            "/rest/v1/rpc/reserve_job",
            json={
                "p_user_id": user_id,
                "p_mode": "single",
                "p_file_ids": [upload_id],
                "p_max_rounds": 1,
                "p_languages": ["zh", "en"],
                "p_research_brief": "",
            },
        )
        job = job_response.json()
        print(
            json.dumps(
                {
                    "job_id": job[0]["id"] if isinstance(job, list) else job["id"],
                    "file": pdf_path.name,
                    "rounds": 1,
                    "starting_spend_cny": round(spend, 2),
                },
                ensure_ascii=False,
            )
        )
    except Exception:
        if storage_path:
            try:
                await repository._request(
                    "DELETE",
                    "/storage/v1/object/papers",
                    json={"prefixes": [storage_path]},
                )
                await repository._request(
                    "DELETE", f"/rest/v1/uploads?id=eq.{quote(upload_id)}"
                )
            except Exception:
                pass
        raise
    finally:
        await repository.close()


if __name__ == "__main__":
    asyncio.run(main())
