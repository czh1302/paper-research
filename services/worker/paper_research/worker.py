from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress

from .clients.supabase import SupabaseRepository
from .config import Settings
from .models import JobStatus
from .pipeline import AnalysisPipeline, BudgetBlocked, JobCancelled
from .security import redact

LOGGER = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings: Settings) -> None:
        settings.require_worker_secrets()
        self.settings = settings
        self.repository = SupabaseRepository(
            settings.SUPABASE_URL or "",
            Settings.reveal(settings.SUPABASE_SERVICE_ROLE_KEY) or "",
        )
        self.pipeline = AnalysisPipeline(settings, self.repository)
        self._stopping = asyncio.Event()
        self._last_cleanup = 0.0

    def stop(self) -> None:
        self._stopping.set()

    async def close(self) -> None:
        await self.pipeline.close()
        await self.repository.close()

    async def _heartbeat(self, job_id: str) -> None:
        interval = max(30, self.settings.JOB_LEASE_SECONDS // 3)
        while True:
            await asyncio.sleep(interval)
            await self.repository.renew_lease(
                job_id, self.settings.WORKER_ID, self.settings.JOB_LEASE_SECONDS
            )

    async def _maybe_cleanup(self) -> None:
        if time.monotonic() - self._last_cleanup < 3600:
            return
        counts = await self.repository.cleanup_expired()
        self._last_cleanup = time.monotonic()
        if counts["uploads"] or counts["reports"] or counts.get("orphans"):
            LOGGER.info("Expired data cleanup: %s", counts)

    async def run_forever(self) -> None:
        LOGGER.info("Worker %s started", self.settings.WORKER_ID)
        try:
            while not self._stopping.is_set():
                try:
                    await self._maybe_cleanup()
                    job = await self.repository.claim_next_job(
                        self.settings.WORKER_ID, self.settings.JOB_LEASE_SECONDS
                    )
                    if not job:
                        await asyncio.sleep(self.settings.POLL_INTERVAL_SECONDS)
                        continue
                    LOGGER.info("Claimed job %s", job.id)
                    heartbeat = asyncio.create_task(self._heartbeat(job.id))
                    try:
                        await self.pipeline.run_job(job)
                    except JobCancelled as error:
                        await self.repository.finish_job(job.id, JobStatus.CANCELLED, str(error))
                    except BudgetBlocked as error:
                        await self.repository.finish_job(
                            job.id, JobStatus.BUDGET_BLOCKED, str(error)
                        )
                    except Exception as error:
                        safe_error = redact(str(error))[:2000]
                        LOGGER.exception("Job %s failed: %s", job.id, safe_error)
                        await self.repository.finish_job(job.id, JobStatus.FAILED, safe_error)
                    else:
                        await self.repository.finish_job(job.id, JobStatus.COMPLETED)
                        await self.repository.add_event(
                            job.id,
                            "source_retained",
                            "Source PDFs retained privately until the task is deleted",
                        )
                    finally:
                        heartbeat.cancel()
                        with suppress(asyncio.CancelledError):
                            await heartbeat
                except Exception as error:
                    LOGGER.exception("Worker loop error: %s", redact(str(error)))
                    await asyncio.sleep(self.settings.POLL_INTERVAL_SECONDS)
        finally:
            await self.close()
