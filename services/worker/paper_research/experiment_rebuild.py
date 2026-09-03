from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .clients.e2b import E2BSandboxProvider, SandboxNotFoundError
from .clients.supabase import SupabaseRepository
from .config import Settings


def _runtime_ids(checkpoint: dict[str, Any], runtime: dict[str, Any] | None) -> list[str]:
    values = [
        str((runtime or {}).get("sandbox_id") or ""),
        str(checkpoint.get("sandbox_id") or ""),
        str(checkpoint.get("automaticSubjectSandboxId") or ""),
        str(checkpoint.get("automaticEvaluatorSandboxId") or ""),
        str(checkpoint.get("automaticEvaluatorPreparedSandboxId") or ""),
    ]
    return list(dict.fromkeys(value for value in values if value))


async def rebuild_experiment_repositories(
    settings: Settings,
    experiment_ids: list[str],
    *,
    reason: str,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    settings.require_experiment_secrets()
    repository = SupabaseRepository(
        settings.SUPABASE_URL or "",
        Settings.reveal(settings.SUPABASE_SERVICE_ROLE_KEY) or "",
    )
    provider = E2BSandboxProvider(
        Settings.reveal(settings.E2B_API_KEY) or "",
        template_id=settings.E2B_TEMPLATE_ID,
        cpu_count=settings.E2B_CPU_COUNT,
        memory_mib=settings.E2B_MEMORY_MIB,
        disk_mib=settings.E2B_DISK_MIB,
        run_timeout_seconds=settings.E2B_RUN_TIMEOUT_SECONDS,
    )
    output: list[dict[str, Any]] = []
    try:
        for experiment_id in list(dict.fromkeys(experiment_ids)):
            experiment = await repository.load_experiment(experiment_id)
            checkpoint = dict(experiment.checkpoint or {})
            runtime = await repository.load_experiment_runtime(experiment_id)
            sandbox_ids = _runtime_ids(checkpoint, runtime)
            record = {
                "experiment_id": experiment_id,
                "status_before": str(experiment.status),
                "stage_before": str(experiment.stage),
                "repository_source": checkpoint.get("repository_generation_source"),
                "current_revision_id": experiment.current_revision_id,
                "llm_cost_cny": experiment.llm_cost_cny,
                "remaining_repository_budget_cny": round(
                    max(0.0, 5.0 - float(experiment.llm_cost_cny)), 6
                ),
                "sandbox_count": len(sandbox_ids),
                "dry_run": dry_run,
            }
            if dry_run:
                output.append(record)
                continue

            for sandbox_id in sandbox_ids:
                try:
                    await provider.kill(sandbox_id)
                except SandboxNotFoundError:
                    pass

            if runtime and runtime.get("sandbox_id"):
                now = datetime.now(timezone.utc).isoformat()
                worker_id = str(experiment.worker_id or "")
                marked = False
                if worker_id:
                    try:
                        await repository.save_claimed_experiment_runtime(
                            experiment_id,
                            worker_id=worker_id,
                            state="destroyed",
                            sandbox_id=str(runtime["sandbox_id"]),
                            last_heartbeat_at=now,
                            metadata={
                                **dict(runtime.get("metadata") or {}),
                                "destroy_reason": "repository_quality_rebuild",
                            },
                            estimated_cost_per_second_usd=settings.E2B_ESTIMATED_COST_PER_SECOND_USD,
                            reserve_seconds=settings.E2B_RUN_TIMEOUT_SECONDS,
                            max_spend_usd=settings.E2B_MAX_SPEND_USD,
                            max_concurrency=settings.E2B_GLOBAL_CONCURRENCY,
                        )
                        marked = True
                    except Exception:
                        # The Worker lease can expire between physical cleanup
                        # and accounting. The sandbox is already gone; persist
                        # the non-billable terminal state before requeueing.
                        marked = False
                if not marked:
                    await repository.save_experiment_runtime(
                        experiment_id,
                        state="destroyed",
                        sandbox_id=str(runtime["sandbox_id"]),
                        active_started_at=None,
                        reserved_until=None,
                        destroy_after=now,
                        last_heartbeat_at=now,
                        pty_session_id=None,
                        controller_token_hash=None,
                        terminal_ticket_hash=None,
                        terminal_ticket_mode=None,
                        terminal_ticket_expires_at=None,
                        metadata={
                            **dict(runtime.get("metadata") or {}),
                            "destroy_reason": "repository_quality_rebuild",
                        },
                    )

            rebuilt = await repository.requeue_experiment_repository_rebuild(
                experiment_id,
                reason=reason,
            )
            local_checkpoint = (
                Path(settings.ARTIFACT_ROOT)
                / "experiment-checkpoints"
                / f"{experiment_id}.json"
            )
            if local_checkpoint.is_file():
                archive = local_checkpoint.with_name(
                    f"{experiment_id}.superseded-repository-"
                    f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
                )
                local_checkpoint.replace(archive)
            record.update(
                {
                    "status_after": str(rebuilt.status),
                    "stage_after": str(rebuilt.stage),
                    "progress_after": rebuilt.progress,
                    "current_revision_after": rebuilt.current_revision_id,
                }
            )
            output.append(record)
    finally:
        await repository.close()
    return output


def format_rebuild_result(values: list[dict[str, Any]]) -> str:
    return json.dumps(values, ensure_ascii=False, indent=2)
