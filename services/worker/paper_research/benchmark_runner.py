from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from .clients.supabase import SupabaseRepository
from .config import Settings
from .document import validate_pdf
from .security import redact

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SITE_URL = "https://czh1302.github.io/paper-research/"
ACTIVE_JOB_STATUSES = {
    "queued",
    "parsing",
    "problem_ready",
    "searching",
    "analyzing",
    "rendering",
    "recovering",
    "waiting_resources",
}
TERMINAL_JOB_STATUSES = {
    "completed",
    "cancelled",
    "failed",
    "budget_blocked",
    "needs_input",
}
TERMINAL_EXPERIMENT_STATUSES = {"ready", "cancelled"}
DEGRADED_EXPERIMENT_OUTCOMES = {
    "environment_blocked",
    "resource_limited",
    "budget_blocked",
}
RETRY_DELAYS_SECONDS = (30, 120, 600, 1800, 7200, 21600)
PAPER_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class BenchmarkManifestError(ValueError):
    pass


class BenchmarkRepository(Protocol):
    async def _request(self, method: str, path: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class BenchmarkPaper:
    id: str
    title: str
    area: str
    path: Path
    sha256: str
    pages: int
    size_bytes: int
    development_exposed: bool


@dataclass(frozen=True)
class BenchmarkManifest:
    name: str
    version: str
    path: Path
    sha256: str
    papers: tuple[BenchmarkPaper, ...]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat()


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _atomic_json(path: Path, payload: Any) -> None:
    _atomic_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
    )


def _json_file_is_valid(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


def _managed_output_is_valid(kind: str, path: Path) -> bool:
    if not _json_file_is_valid(path):
        return False
    try:
        if kind == "baseline":
            from .models import AnalysisReport

            AnalysisReport.model_validate_json(path.read_text(encoding="utf-8"))
        elif kind == "metrics":
            from .benchmark_metrics import PaperMetricRecordProxy

            PaperMetricRecordProxy.model_validate_json(path.read_text(encoding="utf-8"))
        else:
            return False
    except (OSError, UnicodeError, ValueError):
        return False
    return True


def _inside_project(path: Path, project_root: Path) -> bool:
    try:
        path.relative_to(project_root)
    except ValueError:
        return False
    return True


def load_benchmark_manifest(
    manifest_path: Path,
    *,
    project_root: Path = PROJECT_ROOT,
    expected_papers: int = 6,
) -> BenchmarkManifest:
    """Validate the frozen corpus before any remote or paid operation."""

    path = manifest_path.resolve()
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except FileNotFoundError as error:
        raise BenchmarkManifestError(f"Benchmark manifest does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise BenchmarkManifestError(f"Benchmark manifest is not valid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise BenchmarkManifestError("Benchmark manifest must be a JSON object")
    name = payload.get("name")
    version = payload.get("version")
    rows = payload.get("papers")
    if not isinstance(name, str) or not name.strip():
        raise BenchmarkManifestError("Benchmark manifest requires a name")
    if not isinstance(version, str) or not version.strip():
        raise BenchmarkManifestError("Benchmark manifest requires a version")
    if not isinstance(rows, list) or len(rows) != expected_papers:
        raise BenchmarkManifestError(
            f"Benchmark manifest must contain exactly {expected_papers} papers"
        )

    root = project_root.resolve()
    papers: list[BenchmarkPaper] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise BenchmarkManifestError(f"Paper {index} must be an object")
        paper_id = str(row.get("id") or "")
        if not PAPER_ID_PATTERN.fullmatch(paper_id):
            raise BenchmarkManifestError(f"Paper {index} has an invalid id")
        if paper_id in seen_ids:
            raise BenchmarkManifestError(f"Duplicate paper id: {paper_id}")
        seen_ids.add(paper_id)
        local_path = row.get("local_path")
        if not isinstance(local_path, str) or not local_path:
            raise BenchmarkManifestError(f"Paper {paper_id} has no local_path")
        candidate = Path(local_path)
        pdf_path = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if not _inside_project(pdf_path, root):
            raise BenchmarkManifestError(
                f"Paper {paper_id} resolves outside the project workspace"
            )
        try:
            size_bytes, actual_pages = validate_pdf(pdf_path)
        except (OSError, ValueError) as error:
            raise BenchmarkManifestError(f"Paper {paper_id}: {error}") from error
        expected_hash = str(row.get("sha256") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise BenchmarkManifestError(f"Paper {paper_id} has an invalid SHA-256")
        actual_hash = _sha256_path(pdf_path)
        if actual_hash != expected_hash:
            raise BenchmarkManifestError(
                f"Paper {paper_id} SHA-256 mismatch: expected {expected_hash}, got {actual_hash}"
            )
        if actual_hash in seen_hashes:
            raise BenchmarkManifestError(f"Duplicate PDF content for paper {paper_id}")
        seen_hashes.add(actual_hash)
        expected_pages = row.get("pages")
        if not isinstance(expected_pages, int) or expected_pages != actual_pages:
            raise BenchmarkManifestError(
                f"Paper {paper_id} page mismatch: expected {expected_pages}, got {actual_pages}"
            )
        title = row.get("title")
        area = row.get("area")
        if not isinstance(title, str) or not title.strip():
            raise BenchmarkManifestError(f"Paper {paper_id} requires a title")
        if not isinstance(area, str) or not area.strip():
            raise BenchmarkManifestError(f"Paper {paper_id} requires an area")
        papers.append(
            BenchmarkPaper(
                id=paper_id,
                title=title.strip(),
                area=area.strip(),
                path=pdf_path,
                sha256=actual_hash,
                pages=actual_pages,
                size_bytes=size_bytes,
                development_exposed=bool(row.get("development_exposed", False)),
            )
        )
    return BenchmarkManifest(
        name=name.strip(),
        version=version.strip(),
        path=path,
        sha256=hashlib.sha256(raw).hexdigest(),
        papers=tuple(papers),
    )


def retry_delay_seconds(paper_id: str, attempt: int) -> int:
    """Stable 20% jitter keeps a resumed supervisor on the same schedule."""

    base = RETRY_DELAYS_SECONDS[min(max(attempt - 1, 0), len(RETRY_DELAYS_SECONDS) - 1)]
    seed = hashlib.sha256(f"{paper_id}:{attempt}".encode()).digest()[0] / 255
    return max(1, round(base * (0.8 + seed * 0.4)))


def _process_start_token(pid: int) -> str | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    return fields[21] if len(fields) > 21 else None


def _process_matches(pid: int, start_token: object, marker: str) -> bool:
    if pid <= 0 or str(start_token or "") != str(_process_start_token(pid) or ""):
        return False
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", errors="replace"
        )
    except (FileNotFoundError, PermissionError, OSError):
        return False
    return marker in command


def validate_benchmark_settings(settings: Settings, *, include_baseline: bool) -> None:
    settings.require_worker_secrets()
    problems: list[str] = []
    if not settings.IDEA_PIPELINE_V4:
        problems.append("IDEA_PIPELINE_V4=true")
    if settings.V4_MAX_IDEA_REVIEW_ATTEMPTS != 3:
        problems.append("V4_MAX_IDEA_REVIEW_ATTEMPTS=3")
    if not settings.V4_REQUIRE_PILOT_FOR_ALL_REPORTED_IDEAS:
        problems.append("V4_REQUIRE_PILOT_FOR_ALL_REPORTED_IDEAS=true")
    if not settings.E2B_PILOT_ENABLED:
        problems.append("E2B_PILOT_ENABLED=true")
    if not settings.E2B_AUTO_EXPERIMENT_ENABLED:
        problems.append("E2B_AUTO_EXPERIMENT_ENABLED=true")
    if settings.BUDGET_GUARD_CNY > 0:
        problems.append("BUDGET_GUARD_CNY=0")
    if include_baseline and (not settings.DEEPSEEK_API_KEY or not settings.MINERU_API_TOKEN):
        problems.append("baseline model and MinerU credentials")
    if problems:
        raise ValueError("Benchmark production configuration is not ready: " + ", ".join(problems))


class BenchmarkSupervisor:
    """Durable production supervisor; it owns no analysis or E2B lease itself."""

    def __init__(
        self,
        settings: Settings,
        manifest: BenchmarkManifest,
        output: Path,
        owner_job_id: str,
        *,
        repository: BenchmarkRepository,
        include_baseline: bool = True,
        resume: bool = False,
        analysis_concurrency: int = 2,
        baseline_concurrency: int = 2,
        judge_concurrency: int = 2,
        poll_seconds: float = 15,
        site_url: str = DEFAULT_SITE_URL,
        project_root: Path = PROJECT_ROOT,
    ) -> None:
        if analysis_concurrency != 2:
            raise ValueError("The frozen benchmark requires analysis concurrency 2")
        if not 1 <= baseline_concurrency <= 4 or not 1 <= judge_concurrency <= 4:
            raise ValueError("Baseline and judge concurrency must be between 1 and 4")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.settings = settings
        self.manifest = manifest
        self.output = output.resolve()
        self.owner_job_id = owner_job_id
        self.repository = repository
        self.include_baseline = include_baseline
        self.resume = resume
        self.analysis_concurrency = analysis_concurrency
        self.baseline_concurrency = baseline_concurrency
        self.judge_concurrency = judge_concurrency
        self.poll_seconds = poll_seconds
        self.site_url = site_url.rstrip("/") + "/"
        self.project_root = project_root.resolve()
        self.state_path = self.output / "run-state.json"
        self.jobs_path = self.output / "jobs.json"
        self.state = self._load_or_initialize_state()
        self._children: dict[tuple[str, str], asyncio.subprocess.Process] = {}

    def _new_state(self) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        return {
            "schema_version": 1,
            "run_id": run_id,
            "manifest": {
                "name": self.manifest.name,
                "version": self.manifest.version,
                "path": str(self.manifest.path),
                "sha256": self.manifest.sha256,
            },
            "owner_from_job": self.owner_job_id,
            "owner_user_id": None,
            "cold": True,
            "status": "initializing",
            "created_at": _iso(),
            "updated_at": _iso(),
            "configuration": {
                "analysis_concurrency": self.analysis_concurrency,
                "baseline_concurrency": self.baseline_concurrency,
                "judge_concurrency": self.judge_concurrency,
                "include_baseline": self.include_baseline,
            },
            "papers": {
                paper.id: {
                    "id": paper.id,
                    "title": paper.title,
                    "area": paper.area,
                    "pdf": str(paper.path),
                    "sha256": paper.sha256,
                    "pages": paper.pages,
                    "development_exposed": paper.development_exposed,
                    "production": {"status": "not_submitted"},
                    "baseline": {
                        "status": "pending" if self.include_baseline else "skipped",
                        "attempts": 0,
                    },
                    "metrics": {"status": "pending", "attempts": 0},
                }
                for paper in self.manifest.papers
            },
        }

    def _load_or_initialize_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            if not self.resume:
                raise ValueError(
                    f"Benchmark output already has run-state.json; pass --resume: {self.output}"
                )
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                raise ValueError(f"Benchmark run-state is corrupt: {error}") from error
            if state.get("manifest", {}).get("sha256") != self.manifest.sha256:
                raise ValueError("The resumed run uses a different manifest")
            if state.get("owner_from_job") != self.owner_job_id:
                raise ValueError("The resumed run uses a different owner source job")
            return state
        self.output.mkdir(parents=True, exist_ok=True)
        for marker in ("SUCCESS", "DEGRADED", "INCOMPLETE"):
            (self.output / marker).unlink(missing_ok=True)
        state = self._new_state()
        _atomic_json(self.state_path, state)
        return state

    def _save(self) -> None:
        self.state["updated_at"] = _iso()
        _atomic_json(self.state_path, self.state)
        self._write_public_outputs()

    async def _resolve_owner(self) -> str:
        existing = self.state.get("owner_user_id")
        if isinstance(existing, str) and existing:
            return existing
        response = await self.repository._request(
            "GET",
            f"/rest/v1/jobs?id=eq.{quote(self.owner_job_id, safe='')}&select=id,user_id&limit=1",
        )
        rows = response.json() or []
        if not rows:
            raise ValueError("--owner-from-job does not identify an existing production job")
        owner_id = str(rows[0]["user_id"])
        self.state["owner_user_id"] = owner_id
        self._save()
        return owner_id

    async def _submit_one(self, paper: BenchmarkPaper, owner_id: str) -> None:
        entry = self.state["papers"][paper.id]
        production = entry["production"]
        if production.get("job_id"):
            return
        upload_id = str(production.get("upload_id") or uuid.uuid4())
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", paper.path.name)
        storage_path = str(
            production.get("storage_path")
            or f"{owner_id}/{upload_id}/benchmark-{paper.id}-{safe_name}"
        )
        production.update(
            {
                "status": "uploading",
                "upload_id": upload_id,
                "storage_path": storage_path,
            }
        )
        self._save()

        encoded_path = "/".join(quote(part, safe="") for part in storage_path.split("/"))
        # Inputs are capped at 50 MiB by validate_pdf().  A direct read avoids
        # leaving a default asyncio executor behind when a short-lived CLI is
        # interrupted immediately after submission.
        content = paper.path.read_bytes()
        await self.repository._request(
            "POST",
            f"/storage/v1/object/papers/{encoded_path}",
            headers={"Content-Type": "application/pdf", "x-upsert": "true"},
            content=content,
        )
        await self.repository._request(
            "POST",
            "/rest/v1/uploads?on_conflict=id",
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json={
                "id": upload_id,
                "user_id": owner_id,
                "storage_path": storage_path,
                "original_name": paper.path.name,
                "size_bytes": paper.size_bytes,
                "mime_type": "application/pdf",
                "sha256": paper.sha256,
                "status": "uploaded",
            },
        )
        response = await self.repository._request(
            "POST",
            "/rest/v1/rpc/reserve_benchmark_job",
            json={
                "p_owner_job_id": self.owner_job_id,
                "p_upload_id": upload_id,
                "p_benchmark_run_id": self.state["run_id"],
                "p_paper_id": paper.id,
                "p_max_rounds": 1,
                "p_languages": ["zh", "en"],
            },
        )
        payload = response.json()
        row = payload[0] if isinstance(payload, list) else payload
        if not isinstance(row, dict) or not row.get("id"):
            raise RuntimeError(f"Benchmark reservation returned no job for {paper.id}")
        production.update(
            {
                "job_id": str(row["id"]),
                "status": str(row.get("status") or "queued"),
                "stage": str(row.get("stage") or "queued"),
                "progress": int(row.get("progress") or 0),
                "submitted_at": _iso(),
                "job_url": f"{self.site_url}#/jobs/{row['id']}",
            }
        )
        self._save()

    async def submit_cold_jobs(self) -> None:
        owner_id = await self._resolve_owner()
        for paper in self.manifest.papers:
            await self._submit_one(paper, owner_id)
        self.state["status"] = "running"
        self.state["submitted_at"] = self.state.get("submitted_at") or _iso()
        self._save()

    async def _refresh_jobs_and_reports(self) -> None:
        job_ids = [
            row["production"].get("job_id")
            for row in self.state["papers"].values()
            if row["production"].get("job_id")
        ]
        if not job_ids:
            return
        encoded = ",".join(quote(str(value), safe="") for value in job_ids)
        response = await self.repository._request(
            "GET",
            "/rest/v1/jobs"
            f"?id=in.({encoded})"
            "&select=id,status,stage,progress,current_round,retry_count,next_retry_at,updated_at,completed_at",
        )
        rows = {str(row["id"]): row for row in response.json() or []}
        for entry in self.state["papers"].values():
            production = entry["production"]
            job_id = production.get("job_id")
            if not job_id:
                continue
            row = rows.get(str(job_id))
            if not row:
                missing_polls = int(production.get("missing_polls") or 0) + 1
                production["missing_polls"] = missing_polls
                if missing_polls >= 3:
                    production.update(
                        {
                            "status": "missing",
                            "terminal": True,
                            "safe_error": "Production job is missing after three confirmed polls",
                        }
                    )
                continue
            production.pop("missing_polls", None)
            production.update(
                {
                    "status": row.get("status"),
                    "stage": row.get("stage"),
                    "progress": row.get("progress"),
                    "current_round": row.get("current_round"),
                    "retry_count": row.get("retry_count"),
                    "next_retry_at": row.get("next_retry_at"),
                    "updated_at": row.get("updated_at"),
                    "completed_at": row.get("completed_at"),
                    "terminal": row.get("status") in TERMINAL_JOB_STATUSES,
                }
            )

        completed_ids = [
            row["production"]["job_id"]
            for row in self.state["papers"].values()
            if row["production"].get("status") == "completed"
            and not row["production"].get("report_id")
        ]
        if completed_ids:
            report_ids = ",".join(quote(str(value), safe="") for value in completed_ids)
            report_response = await self.repository._request(
                "GET",
                "/rest/v1/reports"
                f"?job_id=in.({report_ids})"
                "&select=id,job_id,generation_id,content,markdown,summary",
            )
            reports = {str(row["job_id"]): row for row in report_response.json() or []}
            for paper_id, entry in self.state["papers"].items():
                production = entry["production"]
                report = reports.get(str(production.get("job_id")))
                if not report:
                    continue
                report_directory = self.output / "papers" / paper_id / "production"
                _atomic_json(report_directory / "report.json", report.get("content") or {})
                _atomic_json(report_directory / "summary.json", report.get("summary") or {})
                _atomic_text(report_directory / "report.md", str(report.get("markdown") or ""))
                production.update(
                    {
                        "report_id": str(report["id"]),
                        "generation_id": str(report.get("generation_id") or ""),
                        "report_url": f"{self.site_url}#/reports/{report['id']}",
                        "report_downloaded_at": _iso(),
                    }
                )

    async def _refresh_experiments(self) -> None:
        report_ids = [
            entry["production"].get("report_id")
            for entry in self.state["papers"].values()
            if entry["production"].get("report_id")
        ]
        if not report_ids:
            return
        encoded = ",".join(quote(str(value), safe="") for value in report_ids)
        response = await self.repository._request(
            "GET",
            "/rest/v1/idea_experiments"
            f"?report_id=in.({encoded})&idea_rank=eq.1"
            "&deletion_requested_at=is.null"
            "&select=id,report_id,report_generation_id,automatic_initial_run,status,stage,progress,outcome,retry_count,next_retry_at,updated_at,completed_at"
            "&order=created_at.desc",
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in response.json() or []:
            grouped.setdefault(str(row["report_id"]), []).append(row)
        for entry in self.state["papers"].values():
            production = entry["production"]
            report_id = production.get("report_id")
            generation_id = production.get("generation_id")
            candidates = [
                row
                for row in grouped.get(str(report_id), [])
                if str(row.get("report_generation_id") or "") == str(generation_id or "")
            ]
            if not candidates:
                production["experiment"] = {"status": "awaiting_enqueue"}
                continue
            experiment = next(
                (row for row in candidates if row.get("automatic_initial_run")), candidates[0]
            )
            experiment_id = str(experiment["id"])
            production["experiment"] = {
                "id": experiment_id,
                "status": experiment.get("status"),
                "stage": experiment.get("stage"),
                "progress": experiment.get("progress"),
                "outcome": experiment.get("outcome"),
                "retry_count": experiment.get("retry_count"),
                "next_retry_at": experiment.get("next_retry_at"),
                "updated_at": experiment.get("updated_at"),
                "completed_at": experiment.get("completed_at"),
                "automatic_initial_run": bool(experiment.get("automatic_initial_run")),
                "workspace_url": f"{self.site_url}#/experiments/{experiment_id}",
            }

    def _baseline_output(self, paper_id: str) -> Path:
        return self.output / "papers" / paper_id / "baseline"

    def _metrics_output(self, paper_id: str) -> Path:
        return self.output / "metrics" / f"{paper_id}.json"

    def _command_for(self, paper: BenchmarkPaper, kind: str) -> tuple[list[str], Path, str]:
        if kind == "baseline":
            output = self._baseline_output(paper.id)
            command = [
                sys.executable,
                "-m",
                "paper_research.main",
                "baseline-local",
                str(paper.path),
                "--output",
                str(output),
            ]
            return command, output / "report.json", str(output)
        if kind != "metrics":
            raise ValueError(f"Unknown managed command kind: {kind}")
        production_report = self.output / "papers" / paper.id / "production" / "report.json"
        baseline_report = self._baseline_output(paper.id) / "report.json"
        output = self._metrics_output(paper.id)
        command = [
            sys.executable,
            str(self.project_root / "benchmark" / "evaluate_teacher.py"),
            "--production-report",
            str(production_report),
            "--baseline-report",
            str(baseline_report),
            "--paper-id",
            paper.id,
            "--pdf",
            str(paper.path),
            "--output",
            str(output),
            "--resume",
            "--repetitions",
            "3",
        ]
        return command, output, str(output)

    def _child_state(self, paper_id: str, kind: str) -> dict[str, Any]:
        return self.state["papers"][paper_id][kind]

    async def _inspect_managed_commands(self, kind: str) -> None:
        for paper in self.manifest.papers:
            child = self._child_state(paper.id, kind)
            if child.get("status") != "running":
                continue
            command, expected, marker = self._command_for(paper, kind)
            del command
            key = (paper.id, kind)
            process = self._children.get(key)
            if process is not None:
                if process.returncode is None:
                    try:
                        await asyncio.wait_for(process.wait(), timeout=0.001)
                    except TimeoutError:
                        continue
                return_code = process.returncode
                self._children.pop(key, None)
            else:
                pid = int(child.get("pid") or 0)
                if _process_matches(pid, child.get("process_start_token"), marker):
                    continue
                return_code = None
            if _managed_output_is_valid(kind, expected):
                child.update(
                    {
                        "status": "completed",
                        "completed_at": _iso(),
                        "pid": None,
                        "process_start_token": None,
                        "last_return_code": return_code,
                    }
                )
                continue
            log_path = Path(str(child.get("log_path") or ""))
            error = "managed command exited without its expected JSON output"
            if log_path.is_file():
                error = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:])
            child.update(
                {
                    "status": "incomplete" if return_code == 2 else "retrying",
                    "last_return_code": return_code,
                    "last_error": redact(error)[-2000:],
                    "pid": None,
                    "process_start_token": None,
                }
            )
            if child["status"] == "retrying":
                delay = retry_delay_seconds(paper.id, int(child.get("attempts") or 1))
                child["next_retry_at"] = _iso(_utcnow() + timedelta(seconds=delay))

    def _command_is_ready(self, paper: BenchmarkPaper, kind: str) -> bool:
        entry = self.state["papers"][paper.id]
        if kind == "baseline":
            return self.include_baseline
        return (
            entry["production"].get("report_id")
            and entry["baseline"].get("status") == "completed"
        )

    async def _launch_managed_commands(self, kind: str, concurrency: int) -> None:
        running = sum(
            1
            for entry in self.state["papers"].values()
            if entry[kind].get("status") == "running"
        )
        for paper in self.manifest.papers:
            if running >= concurrency:
                break
            child = self._child_state(paper.id, kind)
            if child.get("status") in {"completed", "running", "incomplete", "skipped"}:
                continue
            if not self._command_is_ready(paper, kind):
                continue
            due = _parse_time(child.get("next_retry_at"))
            if due and due > _utcnow():
                continue
            command, expected, marker = self._command_for(paper, kind)
            if _managed_output_is_valid(kind, expected):
                child.update({"status": "completed", "completed_at": _iso()})
                continue
            log_path = self.output / "logs" / f"{paper.id}-{kind}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            child.update(
                {
                    "status": "starting",
                    "attempts": int(child.get("attempts") or 0) + 1,
                    "next_retry_at": None,
                    "log_path": str(log_path),
                    "command_kind": kind,
                }
            )
            self._save()
            with log_path.open("ab") as log:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=self.project_root,
                    stdout=log,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
            child.update(
                {
                    "status": "running",
                    "pid": process.pid,
                    "process_start_token": _process_start_token(process.pid),
                    "marker": marker,
                    "started_at": _iso(),
                }
            )
            self._children[(paper.id, kind)] = process
            running += 1
            self._save()

    async def _manage_subprocesses(self) -> None:
        if self.include_baseline:
            await self._inspect_managed_commands("baseline")
            await self._launch_managed_commands("baseline", self.baseline_concurrency)
        await self._inspect_managed_commands("metrics")
        await self._launch_managed_commands("metrics", self.judge_concurrency)

    def _case_status(self, entry: dict[str, Any]) -> str:
        production = entry["production"]
        production_status = production.get("status")
        if production_status in TERMINAL_JOB_STATUSES - {"completed"} or production_status == "missing":
            return "INCOMPLETE"
        if entry["baseline"].get("status") == "incomplete":
            return "INCOMPLETE"
        if entry["metrics"].get("status") == "incomplete":
            return "INCOMPLETE"
        if production_status != "completed" or not production.get("report_id"):
            return "RUNNING"
        experiment = production.get("experiment") or {}
        if experiment.get("status") not in TERMINAL_EXPERIMENT_STATUSES:
            return "RUNNING"
        if experiment.get("status") == "cancelled":
            return "INCOMPLETE"
        if entry["baseline"].get("status") not in {"completed", "skipped"}:
            return "RUNNING"
        if entry["metrics"].get("status") != "completed":
            return "RUNNING"
        if experiment.get("outcome") in DEGRADED_EXPERIMENT_OUTCOMES:
            return "DEGRADED"
        return "SUCCESS"

    def _overall_status(self) -> str:
        states = [self._case_status(entry) for entry in self.state["papers"].values()]
        if any(state == "RUNNING" for state in states):
            return "RUNNING"
        if any(state == "INCOMPLETE" for state in states):
            return "INCOMPLETE"
        if any(state == "DEGRADED" for state in states):
            return "DEGRADED"
        return "SUCCESS"

    def _write_public_outputs(self) -> None:
        rows: list[dict[str, Any]] = []
        for entry in self.state["papers"].values():
            production = entry["production"]
            experiment = production.get("experiment") or {}
            rows.append(
                {
                    "paper_id": entry["id"],
                    "title": entry["title"],
                    "development_exposed": entry["development_exposed"],
                    "job_id": production.get("job_id"),
                    "job_url": production.get("job_url"),
                    "job_status": production.get("status"),
                    "stage": production.get("stage"),
                    "progress": production.get("progress"),
                    "report_id": production.get("report_id"),
                    "report_url": production.get("report_url"),
                    "experiment_id": experiment.get("id"),
                    "experiment_status": experiment.get("status"),
                    "experiment_outcome": experiment.get("outcome"),
                    "workspace_url": experiment.get("workspace_url"),
                    "baseline_status": entry["baseline"].get("status"),
                    "metrics_status": entry["metrics"].get("status"),
                    "case_status": self._case_status(entry),
                }
            )
        _atomic_json(
            self.jobs_path,
            {
                "run_id": self.state["run_id"],
                "status": self._overall_status(),
                "updated_at": self.state.get("updated_at"),
                "jobs": rows,
            },
        )
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ["paper_id"])
        writer.writeheader()
        writer.writerows(rows)
        _atomic_text(self.output / "status.csv", stream.getvalue())
        lines = [
            f"# {self.manifest.name}",
            "",
            f"- Run ID: `{self.state['run_id']}`",
            f"- Status: **{self._overall_status()}**",
            f"- Updated: {self.state.get('updated_at')}",
            "- Scores without human gold labels are automatic proxies.",
            "",
            "| Paper | Production | Baseline | Metrics | Experiment | Result |",
            "|---|---|---|---|---|---|",
        ]
        for row in rows:
            label = row["paper_id"] + (" (development-exposed)" if row["development_exposed"] else "")
            lines.append(
                "| "
                + " | ".join(
                    [
                        label,
                        str(row["job_status"] or "pending"),
                        str(row["baseline_status"]),
                        str(row["metrics_status"]),
                        f"{row['experiment_status'] or 'pending'} / {row['experiment_outcome'] or 'pending'}",
                        str(row["case_status"]),
                    ]
                )
                + " |"
            )
        _atomic_text(self.output / "status.md", "\n".join(lines) + "\n")

    def _write_metric_summary(self) -> None:
        if not all(
            entry["metrics"].get("status") == "completed"
            for entry in self.state["papers"].values()
        ):
            return
        from .benchmark_metrics import (
            PaperMetricRecordProxy,
            write_benchmark_metric_outputs,
        )

        records = []
        for paper in self.manifest.papers:
            record = PaperMetricRecordProxy.model_validate_json(
                self._metrics_output(paper.id).read_text(encoding="utf-8")
            )
            if record.paper_id != paper.id:
                raise ValueError(
                    f"Metric artifact paper id mismatch: expected {paper.id}, got {record.paper_id}"
                )
            records.append(
                record.model_copy(update={"held_out": not paper.development_exposed})
            )
        write_benchmark_metric_outputs(
            self.output,
            records,
            metadata={
                "suite": self.manifest.name,
                "suite_version": self.manifest.version,
                "run_id": self.state["run_id"],
                "manifest_sha256": self.manifest.sha256,
                "production_system": "Research Atlas",
                "baseline_system": "one-call baseline",
                "development_exposed_papers": [
                    paper.id for paper in self.manifest.papers if paper.development_exposed
                ],
            },
        )
        self.state["metric_summary"] = {
            "status": "completed",
            "path": str(self.output / "summary.json"),
            "completed_at": self.state.get("metric_summary", {}).get("completed_at")
            or _iso(),
        }

    def _finish(self, status: str) -> None:
        self.state["status"] = status.lower()
        self.state["completed_at"] = _iso()
        for marker in ("SUCCESS", "DEGRADED", "INCOMPLETE"):
            path = self.output / marker
            if marker == status:
                _atomic_text(path, f"{self.state['run_id']} {self.state['completed_at']}\n")
            else:
                path.unlink(missing_ok=True)
        self._save()

    async def run(self) -> int:
        while not all(
            entry["production"].get("job_id")
            for entry in self.state["papers"].values()
        ):
            try:
                await self.submit_cold_jobs()
            except ValueError:
                raise
            except Exception as error:
                recovery = self.state.setdefault("supervisor_recovery", {})
                attempt = int(recovery.get("attempts") or 0) + 1
                delay = retry_delay_seconds("supervisor-submission", attempt)
                recovery.update(
                    {
                        "phase": "submission",
                        "attempts": attempt,
                        "safe_error": redact(str(error))[-2000:],
                        "next_retry_at": _iso(_utcnow() + timedelta(seconds=delay)),
                    }
                )
                self.state["status"] = "recovering"
                self._save()
                await asyncio.sleep(delay)
        self.state.pop("supervisor_recovery", None)
        print(
            json.dumps(
                {
                    "run_id": self.state["run_id"],
                    "jobs": {
                        paper_id: entry["production"].get("job_id")
                        for paper_id, entry in self.state["papers"].items()
                    },
                    "state": str(self.state_path),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        while True:
            try:
                await self._refresh_jobs_and_reports()
                await self._refresh_experiments()
                await self._manage_subprocesses()
                self._write_metric_summary()
                self.state.pop("supervisor_recovery", None)
            except Exception as error:
                recovery = self.state.setdefault("supervisor_recovery", {})
                attempt = int(recovery.get("attempts") or 0) + 1
                delay = retry_delay_seconds("supervisor-poll", attempt)
                recovery.update(
                    {
                        "phase": "polling",
                        "attempts": attempt,
                        "safe_error": redact(str(error))[-2000:],
                        "next_retry_at": _iso(_utcnow() + timedelta(seconds=delay)),
                    }
                )
                self.state["status"] = "recovering"
                self._save()
                await asyncio.sleep(delay)
                continue
            status = self._overall_status()
            self.state["status"] = status.lower()
            self._save()
            if status != "RUNNING":
                self._finish(status)
                return 0 if status in {"SUCCESS", "DEGRADED"} else 2
            await asyncio.sleep(self.poll_seconds)


async def run_benchmark(
    settings: Settings,
    *,
    manifest_path: Path,
    output: Path,
    owner_job_id: str,
    include_baseline: bool,
    resume: bool,
    analysis_concurrency: int,
    baseline_concurrency: int,
    judge_concurrency: int,
    poll_seconds: float,
) -> int:
    validate_benchmark_settings(settings, include_baseline=include_baseline)
    manifest = load_benchmark_manifest(manifest_path)
    repository = SupabaseRepository(
        settings.SUPABASE_URL or "",
        Settings.reveal(settings.SUPABASE_SERVICE_ROLE_KEY) or "",
    )
    try:
        supervisor = BenchmarkSupervisor(
            settings,
            manifest,
            output,
            owner_job_id,
            repository=repository,
            include_baseline=include_baseline,
            resume=resume,
            analysis_concurrency=analysis_concurrency,
            baseline_concurrency=baseline_concurrency,
            judge_concurrency=judge_concurrency,
            poll_seconds=poll_seconds,
        )
        return await supervisor.run()
    finally:
        await repository.close()


def benchmark_status(output: Path) -> dict[str, Any]:
    path = output.resolve() / "jobs.json"
    if not path.is_file():
        raise ValueError(f"Benchmark status does not exist: {path}")
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as error:
        raise ValueError(f"Benchmark status is corrupt: {error}") from error
