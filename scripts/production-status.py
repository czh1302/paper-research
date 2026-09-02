#!/usr/bin/env python3
"""Print only non-sensitive production readiness counters."""

from __future__ import annotations

import asyncio
import json

from paper_research.clients.supabase import SupabaseRepository
from paper_research.config import Settings


async def main() -> None:
    settings = Settings()
    repository = SupabaseRepository(
        settings.SUPABASE_URL or "",
        Settings.reveal(settings.SUPABASE_SERVICE_ROLE_KEY) or "",
    )
    try:
        response = await repository._request(
            "GET",
            "/rest/v1/jobs"
            "?status=in.(queued,parsing,problem_ready,searching,analyzing,rendering,recovering,waiting_resources)"
            "&select=status",
        )
        rows = response.json()
        experiment_response = await repository._request(
            "GET",
            "/rest/v1/idea_experiments"
            "?status=in.(queued,running,recovering,waiting_resources)"
            "&select=status",
        )
        experiments = experiment_response.json()
        print(
            json.dumps(
                {
                    "active_jobs": len(rows),
                    "statuses": [row["status"] for row in rows],
                    "active_experiments": len(experiments),
                    "experiment_statuses": [row["status"] for row in experiments],
                    "monthly_spend_cny": round(await repository.monthly_spend_cny(), 2),
                    "budget_guard_cny": settings.BUDGET_GUARD_CNY,
                },
                ensure_ascii=False,
            )
        )
    finally:
        await repository.close()


if __name__ == "__main__":
    asyncio.run(main())
