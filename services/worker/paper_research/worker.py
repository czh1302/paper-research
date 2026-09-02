from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from contextlib import suppress

from .clients.supabase import SupabaseRepository
from .config import Settings
from .models import JobStatus
from .pipeline import AnalysisPipeline, BudgetBlocked, JobCancelled
from .security import redact

LOGGER = logging.getLogger(__name__)
_CIRCUIT_FAILURE_CATEGORIES = {"network", "model", "mineru", "database", "rate_limit"}


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
        self._last_experiment_enqueue = 0.0
        self._failure_windows: dict[str, deque[float]] = defaultdict(deque)
        self._circuit_open_until: dict[str, float] = {}

    @staticmethod
    def _failure_category(error: Exception) -> str:
        name = type(error).__name__.casefold()
        message = str(error).casefold()
        if isinstance(error, BudgetBlocked):
            return "budget"
        if "mineru" in name or "mineru" in message:
            return "mineru"
        if "claude" in name or "model" in message or "structured output" in message:
            return "model"
        if "database" in message or "postgres" in message or "supabase" in message:
            return "database"
        if "429" in message or "rate limit" in message:
            return "rate_limit"
        if "timeout" in name or "timeout" in message or "http" in name:
            return "network"
        return "worker"

    @staticmethod
    def _needs_replacement_pdf(error: Exception) -> bool:
        message = str(error).casefold()
        return any(
            marker in message
            for marker in (
                "encrypted pdf",
                "password-protected",
                "password protected",
                "incorrect password",
                "damaged pdf",
                "corrupt pdf",
                "invalid pdf",
                "pdf is empty",
            )
        )

    def _retry_delay(self, job_id: str, retry_count: int, category: str) -> int:
        # Checkpoint recovery is intentionally constant-time.  Repeated failures
        # must not make a resumable job disappear for hours merely because its
        # retry counter is old.  The independent circuit breaker below still
        # protects a provider that is failing repeatedly in a short window.
        del job_id, retry_count
        delay = min(30, self.settings.JOB_RETRY_MAX_DELAY_SECONDS)

        now = time.monotonic()
        if category in _CIRCUIT_FAILURE_CATEGORIES:
            failures = self._failure_windows[category]
            failures.append(now)
            while failures and failures[0] < now - 300:
                failures.popleft()
            if len(failures) >= 5:
                self._circuit_open_until[category] = max(
                    self._circuit_open_until.get(category, 0), now + 600
                )
        opened_until = self._circuit_open_until.get(category, 0)
        if opened_until > now:
            delay = max(delay, round(opened_until - now))
        return min(delay, self.settings.JOB_RETRY_MAX_DELAY_SECONDS)

    async def _recover_job(
        self, job_id: str, retry_count: int, error: Exception, *, resources: bool = False
    ) -> None:
        category = self._failure_category(error)
        safe_error = redact(str(error))[:2000]
        if self._needs_replacement_pdf(error):
            await self.repository.schedule_job_retry(
                job_id, JobStatus.NEEDS_INPUT, 1, "invalid_input", safe_error
            )
            return
        delay = self._retry_delay(job_id, retry_count, category)
        status = JobStatus.WAITING_RESOURCES if resources else JobStatus.RECOVERING
        await self.repository.schedule_job_retry(
            job_id, status, delay, category, safe_error
        )
        LOGGER.warning(
            "Job %s scheduled for automatic recovery in %ss (%s)",
            job_id,
            delay,
            category,
        )

    def stop(self) -> None:
        self._stopping.set()

    async def close(self) -> None:
        await self.pipeline.close()
        await self.repository.close()

    async def _heartbeat(self, job_id: str) -> None:
        interval = max(30, self.settings.JOB_LEASE_SECONDS // 3)
        while True:
            await asyncio.sleep(interval)
            try:
                await self.repository.renew_lease(
                    job_id, self.settings.WORKER_ID, self.settings.JOB_LEASE_SECONDS
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # A transient Supabase timeout must not surface after a report
                # has already completed. The next heartbeat retries the lease.
                LOGGER.warning(
                    "Heartbeat renewal failed for job %s: %s",
                    job_id,
                    redact(str(error)),
                )

    async def _maybe_cleanup(self) -> None:
        if time.monotonic() - self._last_cleanup < 3600:
            return
        counts = await self.repository.cleanup_expired()
        self._last_cleanup = time.monotonic()
        if counts["uploads"] or counts["reports"] or counts.get("orphans"):
            LOGGER.info("Expired data cleanup: %s", counts)

    async def _maybe_enqueue_experiments(self, *, force: bool = False) -> None:
        if not (
            self.settings.E2B_PILOT_ENABLED
            and self.settings.E2B_AUTO_EXPERIMENT_ENABLED
        ):
            return
        if not force and time.monotonic() - self._last_experiment_enqueue < 300:
            return
        self._last_experiment_enqueue = time.monotonic()
        for item in await self.repository.pending_primary_experiments():
            try:
                experiment = await self.repository.enqueue_primary_experiment(
                    item["job_id"],
                    item["user_id"],
                    item["idea_key"],
                    max_spend_usd=self.settings.E2B_MAX_SPEND_USD,
                    llm_reservation_cny=self.settings.EXPERIMENT_LLM_MAX_CNY_PER_RUN,
                    global_llm_max_cny=self.settings.EXPERIMENT_LLM_GLOBAL_MAX_CNY,
                )
                await self.repository.mark_primary_experiment_enqueued(
                    item["job_id"], experiment.id
                )
            except Exception as error:
                # The analysis is already complete. Keep the durable pending
                # marker and retry independently without regressing job state.
                LOGGER.warning(
                    "Could not enqueue main Idea experiment for %s yet: %s",
                    item["job_id"],
                    redact(str(error)),
                )

    async def _process_admin_deletion(self) -> None:
        request = await self.repository.claim_admin_deletion_request(
            self.settings.WORKER_ID, self.settings.JOB_LEASE_SECONDS
        )
        if not request:
            return
        request_id = str(request["id"])
        target_kind = str(request["target_kind"])
        target_id = str(request["target_id"])
        attempt = int(request.get("attempt_count") or 1)
        try:
            ready = await self.repository.admin_deletion_target_ready(target_kind, target_id)
            if not ready:
                await self.repository.finish_admin_deletion_request(
                    request_id,
                    self.settings.WORKER_ID,
                    success=False,
                    retry_seconds=30,
                    error="Waiting for the active worker lease to reach a safe checkpoint",
                )
                return
            if target_kind == "job":
                await self.repository.delete_job_permanently(target_id)
            elif target_kind == "user":
                await self.repository.delete_user_permanently(target_id)
            else:
                raise ValueError(f"Unsupported deletion target: {target_kind}")
            await self.repository.finish_admin_deletion_request(
                request_id, self.settings.WORKER_ID, success=True
            )
            LOGGER.info("Completed administrator %s deletion %s", target_kind, target_id)
        except Exception as error:
            retry_seconds = min(21600, 30 * (2 ** min(max(attempt - 1, 0), 9)))
            safe_error = redact(str(error))[:2000]
            await self.repository.finish_admin_deletion_request(
                request_id,
                self.settings.WORKER_ID,
                success=False,
                retry_seconds=retry_seconds,
                error=safe_error,
            )
            LOGGER.warning(
                "Administrator deletion %s interrupted; retrying in %ss: %s",
                request_id,
                retry_seconds,
                safe_error,
            )

    async def run_forever(self) -> None:
        LOGGER.info("Worker %s started", self.settings.WORKER_ID)
        try:
            while not self._stopping.is_set():
                try:
                    await self._process_admin_deletion()
                    await self._maybe_cleanup()
                    await self._maybe_enqueue_experiments()
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
                        if self.settings.JOB_AUTO_RECOVERY_ENABLED:
                            await self._recover_job(
                                job.id, job.retry_count, error, resources=True
                            )
                        else:
                            await self.repository.finish_job(
                                job.id, JobStatus.BUDGET_BLOCKED, str(error)
                            )
                    except Exception as error:
                        safe_error = redact(str(error))[:2000]
                        LOGGER.exception("Job %s interrupted: %s", job.id, safe_error)
                        if self.settings.JOB_AUTO_RECOVERY_ENABLED:
                            await self._recover_job(job.id, job.retry_count, error)
                        else:
                            await self.repository.finish_job(job.id, JobStatus.FAILED, safe_error)
                    else:
                        await self.repository.finish_job(job.id, JobStatus.COMPLETED)
                        await self._maybe_enqueue_experiments(force=True)
                        await self.repository.add_event(
                            job.id,
                            "source_retained",
                            "Source PDFs retained privately until the task is deleted",
                        )
                    finally:
                        heartbeat.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await heartbeat
                except Exception as error:
                    LOGGER.exception("Worker loop error: %s", redact(str(error)))
                    await asyncio.sleep(self.settings.POLL_INTERVAL_SECONDS)
        finally:
            await self.close()
