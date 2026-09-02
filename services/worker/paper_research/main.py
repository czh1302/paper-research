from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import shutil
import signal
import sys
import uuid
from pathlib import Path

from .clients.llm import ClaudeCodeClient
from .clients.local import LocalCheckpointRepository
from .config import DEFAULT_SECRETS_FILE, Settings
from .document import validate_pdf
from .experiment_worker import run_experiment_worker
from .idea_replay import IdeaReplayRunner
from .models import AnalysisMode, AnalysisReport, Job, JobFile, JobStatus
from .pipeline import AnalysisPipeline, estimate_usage_cny
from .reporting import comparison_csv, report_markdown
from .worker import Worker


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # httpx logs full URLs, which may contain provider keys or short-lived signed URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def doctor(settings: Settings) -> int:
    checks = {
        "secrets_file": DEFAULT_SECRETS_FILE.exists(),
        "claude_binary": shutil.which(settings.CLAUDE_BIN) is not None,
        "claude_version_at_least_2_1_248": False,
        "deepseek_key": bool(settings.DEEPSEEK_API_KEY),
        "mineru_token": bool(settings.MINERU_API_TOKEN),
        "supabase": bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY),
        "turnstile_secret": bool(settings.TURNSTILE_SECRET_KEY),
        "crossref_mailto": bool(
            settings.CROSSREF_MAILTO
            and settings.CROSSREF_MAILTO != "research@example.invalid"
        ),
        "openalex": bool(settings.OPENALEX_API_KEY),
        "serper": bool(settings.SERPER_API_KEY),
        "tavily": bool(settings.TAVILY_API_KEY),
        "e2b_key": bool(settings.E2B_API_KEY),
    }
    if checks["claude_binary"]:
        import subprocess

        result = subprocess.run(
            [settings.CLAUDE_BIN, "--version"], capture_output=True, text=True, check=False
        )
        version = (result.stdout or result.stderr).split()[0]
        try:
            parts = tuple(int(part) for part in version.split(".")[:3])
            checks["claude_version_at_least_2_1_248"] = parts >= (2, 1, 248)
        except ValueError:
            pass
    print(
        json.dumps(
            {
                **checks,
                "turnstile_mode": "test" if settings.TURNSTILE_TEST_MODE else "production",
                "llm_transport": "claude_code",
                "flash_model": settings.CLAUDE_MODEL,
                "pro_model": settings.CLAUDE_PRO_MODEL,
                "flash_cli_alias": ClaudeCodeClient._claude_cli_model(
                    settings.CLAUDE_MODEL
                ),
                "pro_cli_alias": ClaudeCodeClient._claude_cli_model(
                    settings.CLAUDE_PRO_MODEL
                ),
                "e2b_pilot_enabled": settings.E2B_PILOT_ENABLED,
                "e2b_manual_experiment_enabled": settings.E2B_MANUAL_EXPERIMENT_ENABLED,
                "e2b_auto_experiment_enabled": settings.E2B_AUTO_EXPERIMENT_ENABLED,
                "e2b_template_id": settings.E2B_TEMPLATE_ID,
                "experiment_worker_id": settings.EXPERIMENT_WORKER_ID,
                "experiment_limits": {
                    "cpu": settings.E2B_CPU_COUNT,
                    "memory_mib": settings.E2B_MEMORY_MIB,
                    "disk_mib": settings.E2B_DISK_MIB,
                    "timeout_seconds": settings.E2B_RUN_TIMEOUT_SECONDS,
                    "global_concurrency": settings.E2B_GLOBAL_CONCURRENCY,
                    "spend_cap_usd": settings.E2B_MAX_SPEND_USD,
                },
            },
            indent=2,
        )
    )
    return 0 if all(checks.values()) else 1


async def analyze_local(settings: Settings, files: list[Path], rounds: int, output: Path) -> int:
    if not 1 <= len(files) <= 5:
        raise ValueError("Provide one to five PDFs")
    if not 1 <= rounds <= 5:
        raise ValueError("Rounds must be between one and five")
    job_files = []
    hashes = []
    for path in files:
        size, _ = validate_pdf(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes.append(digest)
        job_files.append(
            JobFile(
                id=str(uuid.uuid4()),
                storage_path="local",
                original_name=path.name,
                size_bytes=size,
                sha256=digest,
            )
        )
    fingerprint = hashlib.sha256("\n".join(hashes).encode("ascii")).hexdigest()
    job = Job(
        id=f"local-{fingerprint[:32]}",
        user_id="local",
        mode=AnalysisMode.SINGLE if len(files) == 1 else AnalysisMode.MULTI,
        max_rounds=rounds,
        status=JobStatus.QUEUED,
        files=job_files,
    )
    repository = LocalCheckpointRepository(
        output / ".checkpoint.json",
        fingerprint,
        settings.ARTIFACT_ROOT / "provider-usage.jsonl",
    )
    pipeline = AnalysisPipeline(settings, repository)
    try:
        report = await pipeline.analyze_files(job, files, persist=True)
    finally:
        await pipeline.close()
    write_report(report, output)
    return 0


def write_report(report: AnalysisReport, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (output / "report.md").write_text(report_markdown(report), encoding="utf-8")
    (output / "comparison.csv").write_text(comparison_csv(report), encoding="utf-8")
    print(f"Report written to {output}")


async def run_worker(settings: Settings) -> None:
    worker = Worker(settings)
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, worker.stop)
            installed_signals.append(signum)
        except NotImplementedError:  # pragma: no cover - Windows fallback
            pass
    try:
        await worker.run_forever()
    finally:
        for signum in installed_signals:
            loop.remove_signal_handler(signum)


async def analyze_baseline_local(settings: Settings, file: Path, output: Path) -> int:
    size, _ = validate_pdf(file)
    job = Job(
        id=str(uuid.uuid4()),
        user_id="local-baseline",
        mode=AnalysisMode.SINGLE,
        max_rounds=1,
        status=JobStatus.QUEUED,
        files=[
            JobFile(
                id=str(uuid.uuid4()),
                storage_path="local",
                original_name=file.name,
                size_bytes=size,
            )
        ],
    )
    pipeline = AnalysisPipeline(settings)
    try:
        report = await pipeline.analyze_baseline(job, file)
    finally:
        await pipeline.close()
    write_report(report, output)
    return 0


async def replay_ideas_local(
    settings: Settings, checkpoint: Path, output: Path
) -> int:
    if not checkpoint.is_file():
        raise ValueError(f"Idea replay checkpoint does not exist: {checkpoint}")
    output.mkdir(parents=True, exist_ok=True)
    usage_path = output / "provider-usage.jsonl"

    async def record_usage(usage) -> None:
        usage.estimated_cny = estimate_usage_cny(usage)
        with usage_path.open("a", encoding="utf-8") as stream:
            stream.write(usage.model_dump_json() + "\n")

    client = ClaudeCodeClient(
        Settings.reveal(settings.DEEPSEEK_API_KEY) or "mock",
        binary=settings.CLAUDE_BIN,
        model=settings.CLAUDE_MODEL,
        effort=settings.CLAUDE_EFFORT,
        timeout_seconds=settings.CLAUDE_TIMEOUT_SECONDS,
        analysis_max_turns=settings.CLAUDE_ANALYSIS_MAX_TURNS,
        web_max_turns=settings.CLAUDE_WEB_MAX_TURNS,
        usage_callback=record_usage,
    )
    runner = IdeaReplayRunner(
        client,
        classification_model=settings.CLAUDE_MODEL,
        idea_model=settings.CLAUDE_PRO_MODEL,
        output=output,
    )
    result = await runner.run(checkpoint)
    print(f"Idea replay written to {output} ({result.decision})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Paper research worker")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("worker", help="Poll Supabase and process jobs")
    subparsers.add_parser(
        "experiment-worker", help="Run isolated E2B Idea experiments and workspace actions"
    )
    subparsers.add_parser("doctor", help="Check local configuration without network calls")
    local = subparsers.add_parser("analyze-local", help="Run the cloud pipeline without Supabase")
    local.add_argument("files", nargs="+", type=Path)
    local.add_argument("--rounds", type=int, default=1)
    local.add_argument(
        "--search-profile",
        choices=("academic_only", "academic_web"),
        default="academic_web",
    )
    local.add_argument("--output", type=Path, default=Path(".artifacts/local-report"))
    baseline = subparsers.add_parser(
        "baseline-local", help="Run the one-call whole-paper benchmark baseline"
    )
    baseline.add_argument("file", type=Path)
    baseline.add_argument("--output", type=Path, default=Path(".artifacts/baseline-report"))
    replay = subparsers.add_parser(
        "idea-replay", help="Re-run only gap, Idea, and review stages from a V4 checkpoint"
    )
    replay.add_argument("--checkpoint", type=Path, required=True)
    replay.add_argument(
        "--output", type=Path, default=Path(".artifacts/idea-quality-replay")
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(args.verbose)
    settings = Settings()
    try:
        if args.command == "doctor":
            code = doctor(settings)
        elif args.command == "worker":
            settings.require_worker_secrets()
            code = 0
            asyncio.run(run_worker(settings))
        elif args.command == "experiment-worker":
            settings.require_experiment_secrets()
            code = 0
            asyncio.run(run_experiment_worker(settings))
        elif args.command == "analyze-local":
            if not settings.DEEPSEEK_API_KEY or not settings.MINERU_API_TOKEN:
                raise RuntimeError("Rotated DEEPSEEK_API_KEY and MINERU_API_TOKEN are required")
            settings.SEARCH_PROFILE = args.search_profile
            code = asyncio.run(analyze_local(settings, args.files, args.rounds, args.output))
        elif args.command == "baseline-local":
            if not settings.DEEPSEEK_API_KEY or not settings.MINERU_API_TOKEN:
                raise RuntimeError("Rotated DEEPSEEK_API_KEY and MINERU_API_TOKEN are required")
            code = asyncio.run(analyze_baseline_local(settings, args.file, args.output))
        else:
            if not settings.DEEPSEEK_API_KEY:
                raise RuntimeError("A rotated DEEPSEEK_API_KEY is required")
            code = asyncio.run(
                replay_ideas_local(
                    settings,
                    args.checkpoint,
                    args.output,
                )
            )
    except (RuntimeError, ValueError) as error:
        parser.error(str(error))
        return
    sys.exit(code)


if __name__ == "__main__":
    main()
