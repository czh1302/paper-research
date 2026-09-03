"""Local-only supervisor for the compact six-paper benchmark evaluation V2."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from .benchmark_metrics import (
    PaperMetricRecordProxy,
    atomic_write_json,
    atomic_write_text,
    write_benchmark_metric_outputs,
)
from .benchmark_runner import BenchmarkCase, load_benchmark_manifest
from .clients.llm import OpenAICompatibleClient
from .config import Settings
from .security import redact
from .teacher_evaluator import _json_bytes, _pool_payload, compact_report_payload
from .teacher_evaluator_v2 import freeze_primary_items, load_frozen_v1_assets


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _ledger_total(path: Path) -> float:
    if not path.is_file():
        return 0.0
    total = 0.0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            total += float(json.loads(line).get("estimated_usd") or 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return total


def _valid_metric(path: Path, paper_id: str) -> bool:
    try:
        record = PaperMetricRecordProxy.model_validate_json(path.read_text(encoding="utf-8"))
        return (
            record.paper_id == paper_id
            and record.protocol_version == "teacher-benchmark-metrics-v2"
        )
    except (OSError, ValueError):
        return False


class TeacherBenchmarkV2Supervisor:
    def __init__(
        self,
        settings: Settings,
        *,
        manifest_path: Path,
        output: Path,
        concurrency: int = 4,
        poll_seconds: float = 2,
    ) -> None:
        self.settings = settings
        self.manifest = load_benchmark_manifest(manifest_path)
        self.output = output.resolve()
        self.metrics_dir = self.output / "metrics-v2"
        self.logs_dir = self.output / "logs-v2"
        self.state_path = self.metrics_dir / "run-state.json"
        self.usage_path = self.output / "provider-usage-v2.jsonl"
        self.concurrency = min(max(concurrency, 1), 4)
        self.poll_seconds = max(poll_seconds, 1)
        self.children: dict[str, asyncio.subprocess.Process] = {}
        self.state = _read_json(self.state_path) or {
            "schema_version": "teacher-benchmark-supervisor-v2",
            "status": "initializing",
            "started_at": _now(),
            "model": settings.TEACHER_BENCHMARK_MODEL,
            "transport": "openai_compatible",
            "endpoint_host": httpx.URL(settings.TEACHER_BENCHMARK_API_BASE).host,
            "configured_concurrency": self.concurrency,
            "effective_concurrency": 1,
            "canary": {"status": "pending"},
            "cases": {
                case.id: {"status": "pending", "attempts": 0} for case in self.manifest.cases
            },
        }

    def save(self) -> None:
        self.state["updated_at"] = _now()
        self.state["estimated_provider_usd"] = _ledger_total(self.usage_path)
        atomic_write_json(self.state_path, self.state)

    def archive_v1(self) -> None:
        destination = self.output / "metrics-v1-invalid"
        destination.mkdir(parents=True, exist_ok=True)
        old = self.output / "metrics"
        if old.is_dir():
            for source in old.iterdir():
                if source.is_file() and (
                    source.suffix == ".json" or source.name.endswith(".checkpoint.json")
                ):
                    target = destination / source.name
                    if not target.exists():
                        shutil.copy2(source, target)
        atomic_write_text(
            destination / "INVALIDATION.md",
            "# Invalid V1 metric artifacts\n\n"
            "These files are retained for audit only. They are excluded from V2 summaries because "
            "the monolithic judge request silently treated omitted citation IDs as failures and "
            "read full-text availability from stale retrieval candidates.\n",
        )

    async def billing_snapshot(self) -> dict[str, Any] | None:
        key = Settings.reveal(self.settings.RCOUYI_API_KEY)
        if not key:
            return None
        today = date.today()
        params = {
            "start_date": str(
                int(datetime(today.year, today.month, 1, tzinfo=timezone.utc).timestamp())
            ),
            "end_date": str(
                int(
                    datetime.combine(
                        today + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
                    ).timestamp()
                )
            ),
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{self.settings.TEACHER_BENCHMARK_API_BASE.rstrip('/')}/v1/dashboard/billing/usage",
                    headers={"Authorization": f"Bearer {key}"},
                    params=params,
                )
            if response.status_code != 200:
                return {"available": False, "http_status": response.status_code}
            payload = response.json()
            return {
                "available": True,
                "total_usage": payload.get("total_usage") if isinstance(payload, dict) else None,
                "recorded_at": _now(),
            }
        except (httpx.HTTPError, ValueError):
            return {"available": False}

    async def preflight(self) -> None:
        key = Settings.reveal(self.settings.RCOUYI_API_KEY)
        if not key:
            raise RuntimeError("RCOUYI_API_KEY is required")
        if self.settings.TEACHER_BENCHMARK_TRANSPORT != "openai_compatible":
            raise RuntimeError("teacher benchmark transport must be openai_compatible")
        client = OpenAICompatibleClient(
            key,
            model=self.settings.TEACHER_BENCHMARK_MODEL,
            base_url=self.settings.TEACHER_BENCHMARK_API_BASE,
            timeout_seconds=self.settings.TEACHER_BENCHMARK_TIMEOUT_SECONDS,
        )
        models = await client.list_models()
        if self.settings.TEACHER_BENCHMARK_MODEL not in models:
            raise RuntimeError(
                f"required model is unavailable: {self.settings.TEACHER_BENCHMARK_MODEL}"
            )
        artifact_audit: dict[str, Any] = {}
        for case in self.manifest.cases:
            production_path = self.output / "papers" / case.id / "production" / "report.json"
            baseline_path = self.output / "papers" / case.id / "baseline" / "report.json"
            frozen_path = self.output / "metrics" / f".{case.id}.json.checkpoint.json"
            production_raw, production_report = _json_bytes(production_path)
            baseline_raw, baseline_report = _json_bytes(baseline_path)
            production = compact_report_payload(production_report)
            baseline = compact_report_payload(baseline_report)
            pool = _pool_payload(production, baseline)
            load_frozen_v1_assets(
                frozen_path,
                pool=pool,
                source_ids=[paper.id for paper in case.inputs],
            )
            primary = freeze_primary_items(production)
            if len({str(row["item_id"]) for row in primary}) != len(primary):
                raise RuntimeError(f"duplicate primary comparison IDs in {case.id}")
            artifact_audit[case.id] = {
                "production_sha256": hashlib.sha256(production_raw).hexdigest(),
                "baseline_sha256": hashlib.sha256(baseline_raw).hexdigest(),
                "frozen_qrel_count": len(pool),
                "production_primary_item_count": len(primary),
                "max_batch_items": 42,
                "largest_primary_batch_json_bytes": max(
                    (
                        len(
                            json.dumps(
                                primary[index : index + 42],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        )
                        for index in range(0, len(primary), 42)
                    ),
                    default=0,
                ),
            }
        self.state["preflight"] = {
            "status": "passed",
            "model_exact_match": True,
            "checked_at": _now(),
            "billing_before": await self.billing_snapshot(),
            "artifact_audit": artifact_audit,
        }
        self.save()

    def command(self, case: BenchmarkCase) -> list[str]:
        production = self.output / "papers" / case.id / "production" / "report.json"
        baseline = self.output / "papers" / case.id / "baseline" / "report.json"
        frozen = self.output / "metrics" / f".{case.id}.json.checkpoint.json"
        destination = self.metrics_dir / f"{case.id}.json"
        for path in (production, baseline, frozen):
            if not path.is_file():
                raise RuntimeError(f"required frozen artifact is missing: {path}")
        return [
            sys.executable,
            "-m",
            "paper_research.teacher_evaluator_v2",
            "--production-report",
            str(production),
            "--baseline-report",
            str(baseline),
            "--paper-id",
            case.id,
            *[
                value
                for paper in case.inputs
                for value in ("--pdf", str(paper.path), "--source-paper-id", paper.id)
            ],
            "--frozen-checkpoint",
            str(frozen),
            "--output",
            str(destination),
            "--resume",
        ]

    def canary_passed(self) -> bool:
        case = self.manifest.cases[0]
        checkpoint = _read_json(self.metrics_dir / f".{case.id}.json.checkpoint.json") or {}
        calls = checkpoint.get("calls") or {}
        if "report_pair_primary" not in calls:
            return False
        usage_rows = []
        if self.usage_path.is_file():
            for line in self.usage_path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                metadata = row.get("metadata") or {}
                if (
                    row.get("paper_id") == case.id
                    and metadata.get("stage") == "teacher_benchmark_v2.report_pair_primary"
                ):
                    usage_rows.append(row)
        return bool(usage_rows) and all(
            row.get("model") == self.settings.TEACHER_BENCHMARK_MODEL
            and int(row.get("input_tokens") or 0) > 0
            and int(row.get("output_tokens") or 0) > 0
            and (row.get("metadata") or {}).get("transport") == "openai_compatible"
            for row in usage_rows
        )

    async def launch(self, case: BenchmarkCase) -> None:
        entry = self.state["cases"][case.id]
        log_path = self.logs_dir / f"{case.id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry.update(
            {
                "status": "starting",
                "attempts": int(entry.get("attempts") or 0) + 1,
                "log_path": str(log_path),
            }
        )
        self.save()
        with log_path.open("ab") as log:
            process = await asyncio.create_subprocess_exec(
                *self.command(case),
                cwd=Path(__file__).resolve().parents[3],
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        self.children[case.id] = process
        entry.update({"status": "running", "pid": process.pid, "started_at": _now()})
        self.save()

    async def reap(self) -> None:
        for paper_id, process in list(self.children.items()):
            if process.returncode is None:
                continue
            del self.children[paper_id]
            entry = self.state["cases"][paper_id]
            path = self.metrics_dir / f"{paper_id}.json"
            if process.returncode == 0 and _valid_metric(path, paper_id):
                entry.update({"status": "completed", "completed_at": _now(), "pid": None})
                continue
            log_path = Path(entry["log_path"])
            tail = ""
            if log_path.is_file():
                tail = "\n".join(
                    log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
                )
            entry.update(
                {
                    "status": "retrying" if process.returncode != 2 else "invalid_input",
                    "pid": None,
                    "last_return_code": process.returncode,
                    "safe_error": redact(tail)[-1500:],
                    "next_retry_at": (
                        datetime.now(timezone.utc) + timedelta(seconds=30)
                    ).isoformat(),
                }
            )
            if "429" in tail:
                count = int(self.state.get("rate_limit_failures") or 0) + 1
                self.state["rate_limit_failures"] = count
                if count >= 2:
                    self.state["effective_concurrency"] = 2
            self.save()

    def ready(self, entry: dict[str, Any]) -> bool:
        when = entry.get("next_retry_at")
        if not when:
            return True
        try:
            return datetime.fromisoformat(when) <= datetime.now(timezone.utc)
        except ValueError:
            return True

    def finalize(self) -> None:
        records = [
            PaperMetricRecordProxy.model_validate_json(
                (self.metrics_dir / f"{case.id}.json").read_text(encoding="utf-8")
            )
            for case in self.manifest.cases
        ]
        write_benchmark_metric_outputs(
            self.output,
            records,
            metadata={
                "suite": self.manifest.name,
                "suite_version": self.manifest.version,
                "protocol": "teacher-benchmark-metrics-v2",
                "model": self.settings.TEACHER_BENCHMARK_MODEL,
                "transport": "openai_compatible",
                "endpoint_host": httpx.URL(self.settings.TEACHER_BENCHMARK_API_BASE).host,
                "v1_metrics": "metrics-v1-invalid",
            },
            metrics_dir_name="metrics-v2",
        )
        atomic_write_text(self.output / "SUCCESS", f"teacher-benchmark-metrics-v2 {_now()}\n")

    async def run(self) -> int:
        self.metrics_dir.mkdir(parents=True, exist_ok=True)
        self.archive_v1()
        await self.preflight()
        self.state["status"] = "running"
        self.save()
        canary_case = self.manifest.cases[0]
        while True:
            for case in self.manifest.cases:
                path = self.metrics_dir / f"{case.id}.json"
                if _valid_metric(path, case.id):
                    self.state["cases"][case.id]["status"] = "completed"
            await self.reap()
            if not self.canary_passed():
                self.state["effective_concurrency"] = 1
                if canary_case.id not in self.children and self.state["cases"][canary_case.id][
                    "status"
                ] not in {"completed", "invalid_input"}:
                    await self.launch(canary_case)
            else:
                if self.state["canary"].get("status") != "passed":
                    self.state["canary"] = {
                        "status": "passed",
                        "checked_at": _now(),
                        "billing_after": await self.billing_snapshot(),
                    }
                effective = int(self.state.get("effective_concurrency") or self.concurrency)
                if effective == 1 and not self.state.get("rate_limit_failures"):
                    effective = self.concurrency
                    self.state["effective_concurrency"] = effective
                for case in self.manifest.cases:
                    if len(self.children) >= effective:
                        break
                    entry = self.state["cases"][case.id]
                    if entry.get("status") in {
                        "completed",
                        "running",
                        "invalid_input",
                    } or not self.ready(entry):
                        continue
                    await self.launch(case)
            spent = _ledger_total(self.usage_path)
            if spent >= self.settings.TEACHER_BENCHMARK_MAX_PROVIDER_USD:
                self.state["status"] = "budget_stopped"
                self.save()
                for process in self.children.values():
                    if process.returncode is None:
                        process.terminate()
                return 3
            statuses = {entry.get("status") for entry in self.state["cases"].values()}
            if statuses == {"completed"}:
                self.finalize()
                self.state["status"] = "completed"
                self.state["completed_at"] = _now()
                self.save()
                return 0
            if "invalid_input" in statuses:
                self.state["status"] = "invalid_input"
                self.save()
                return 2
            self.save()
            await asyncio.sleep(self.poll_seconds)

    async def preflight_only(self) -> int:
        self.metrics_dir.mkdir(parents=True, exist_ok=True)
        self.archive_v1()
        await self.preflight()
        self.state["status"] = "preflight_passed"
        self.save()
        return 0


async def run_teacher_benchmark_v2(
    settings: Settings,
    *,
    manifest_path: Path,
    output: Path,
    concurrency: int,
    preflight_only: bool = False,
) -> int:
    supervisor = TeacherBenchmarkV2Supervisor(
        settings, manifest_path=manifest_path, output=output, concurrency=concurrency
    )
    return await (supervisor.preflight_only() if preflight_only else supervisor.run())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resume the six-paper compact V2 evaluation")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=4, choices=(1, 2, 3, 4))
    parser.add_argument("--preflight-only", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        code = asyncio.run(
            run_teacher_benchmark_v2(
                Settings(),
                manifest_path=args.manifest,
                output=args.output,
                concurrency=args.concurrency,
                preflight_only=args.preflight_only,
            )
        )
    except (RuntimeError, ValueError) as error:
        print(f"benchmark V2 error: {redact(str(error))}", file=sys.stderr)
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
