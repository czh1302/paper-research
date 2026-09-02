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
from typing import Any, Literal, Protocol
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
CHECKPOINT_RETRY_SECONDS = 30
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
class BenchmarkCase:
    id: str
    title: str
    area: str
    mode: Literal["single", "multi"]
    semantics: Literal["primary", "symmetric"]
    inputs: tuple[BenchmarkPaper, ...]
    development_exposed: bool

    @property
    def path(self) -> Path:
        """Compatibility accessor for the legacy one-paper runner/tests."""

        if len(self.inputs) != 1:
            raise ValueError(f"Benchmark case {self.id} contains multiple PDFs")
        return self.inputs[0].path

    @property
    def sha256(self) -> str:
        if len(self.inputs) != 1:
            raise ValueError(f"Benchmark case {self.id} contains multiple PDFs")
        return self.inputs[0].sha256

    @property
    def pages(self) -> int:
        if len(self.inputs) != 1:
            raise ValueError(f"Benchmark case {self.id} contains multiple PDFs")
        return self.inputs[0].pages

    @property
    def size_bytes(self) -> int:
        if len(self.inputs) != 1:
            raise ValueError(f"Benchmark case {self.id} contains multiple PDFs")
        return self.inputs[0].size_bytes


@dataclass(frozen=True)
class BenchmarkManifest:
    name: str
    version: str
    path: Path
    sha256: str
    cases: tuple[BenchmarkCase, ...]
    uses_case_format: bool = False

    @property
    def papers(self) -> tuple[BenchmarkPaper, ...]:
        """Flattened inputs retained for old manifest consumers and dry-runs."""

        return tuple(item for case in self.cases for item in case.inputs)


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
    legacy_rows = payload.get("papers")
    case_rows = payload.get("cases")
    if not isinstance(name, str) or not name.strip():
        raise BenchmarkManifestError("Benchmark manifest requires a name")
    if not isinstance(version, str) or not version.strip():
        raise BenchmarkManifestError("Benchmark manifest requires a version")
    if legacy_rows is not None and case_rows is not None:
        raise BenchmarkManifestError("Benchmark manifest cannot contain both papers and cases")
    if legacy_rows is not None:
        if not isinstance(legacy_rows, list) or len(legacy_rows) != expected_papers:
            raise BenchmarkManifestError(
                f"Benchmark manifest must contain exactly {expected_papers} papers"
            )
        rows: list[Any] = [
            {
                "id": row.get("id") if isinstance(row, dict) else None,
                "title": row.get("title") if isinstance(row, dict) else None,
                "area": row.get("area") if isinstance(row, dict) else None,
                "mode": "single",
                "semantics": "primary",
                "development_exposed": (
                    row.get("development_exposed", False)
                    if isinstance(row, dict)
                    else False
                ),
                "inputs": [row],
            }
            for row in legacy_rows
        ]
    else:
        if not isinstance(case_rows, list) or not case_rows:
            raise BenchmarkManifestError("Benchmark manifest requires non-empty papers or cases")
        rows = case_rows

    root = project_root.resolve()
    cases: list[BenchmarkCase] = []
    seen_case_ids: set[str] = set()
    seen_input_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for case_index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise BenchmarkManifestError(f"Case {case_index} must be an object")
        case_id = str(row.get("id") or "")
        if not PAPER_ID_PATTERN.fullmatch(case_id):
            raise BenchmarkManifestError(f"Case {case_index} has an invalid id")
        if case_id in seen_case_ids:
            raise BenchmarkManifestError(f"Duplicate case id: {case_id}")
        seen_case_ids.add(case_id)
        mode = str(row.get("mode") or "")
        semantics = str(row.get("semantics") or "")
        input_rows = row.get("inputs")
        if mode not in {"single", "multi"}:
            raise BenchmarkManifestError(f"Case {case_id} has an invalid mode")
        if semantics not in {"primary", "symmetric"}:
            raise BenchmarkManifestError(f"Case {case_id} has invalid semantics")
        if not isinstance(input_rows, list) or not input_rows:
            raise BenchmarkManifestError(f"Case {case_id} requires ordered inputs")
        if len(input_rows) > 5:
            raise BenchmarkManifestError(f"Case {case_id} exceeds the five-PDF product limit")
        if mode == "single" and len(input_rows) != 1:
            raise BenchmarkManifestError(f"Single case {case_id} must contain exactly one input")
        if mode == "multi" and len(input_rows) < 2:
            raise BenchmarkManifestError(f"Multi case {case_id} must contain at least two inputs")
        if mode == "multi" and semantics != "symmetric":
            raise BenchmarkManifestError(f"Multi case {case_id} must use symmetric semantics")
        case_title = row.get("title")
        case_area = row.get("area")
        if not isinstance(case_title, str) or not case_title.strip():
            raise BenchmarkManifestError(f"Case {case_id} requires a title")
        if not isinstance(case_area, str) or not case_area.strip():
            raise BenchmarkManifestError(f"Case {case_id} requires an area")

        inputs: list[BenchmarkPaper] = []
        for position, input_row in enumerate(input_rows, start=1):
            if not isinstance(input_row, dict):
                raise BenchmarkManifestError(
                    f"Case {case_id} input {position} must be an object"
                )
            paper_id = str(input_row.get("id") or "")
            if not PAPER_ID_PATTERN.fullmatch(paper_id):
                raise BenchmarkManifestError(
                    f"Case {case_id} input {position} has an invalid id"
                )
            if paper_id in seen_input_ids:
                raise BenchmarkManifestError(f"Duplicate input paper id: {paper_id}")
            seen_input_ids.add(paper_id)
            local_path = input_row.get("local_path")
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
            expected_hash = str(input_row.get("sha256") or "").lower()
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
            expected_pages = input_row.get("pages")
            if not isinstance(expected_pages, int) or expected_pages != actual_pages:
                raise BenchmarkManifestError(
                    f"Paper {paper_id} page mismatch: expected {expected_pages}, got {actual_pages}"
                )
            paper_title = input_row.get("title")
            paper_area = input_row.get("area", case_area)
            if not isinstance(paper_title, str) or not paper_title.strip():
                raise BenchmarkManifestError(f"Paper {paper_id} requires a title")
            if not isinstance(paper_area, str) or not paper_area.strip():
                raise BenchmarkManifestError(f"Paper {paper_id} requires an area")
            inputs.append(
                BenchmarkPaper(
                    id=paper_id,
                    title=paper_title.strip(),
                    area=paper_area.strip(),
                    path=pdf_path,
                    sha256=actual_hash,
                    pages=actual_pages,
                    size_bytes=size_bytes,
                    development_exposed=bool(
                        input_row.get(
                            "development_exposed", row.get("development_exposed", False)
                        )
                    ),
                )
            )
        cases.append(
            BenchmarkCase(
                id=case_id,
                title=case_title.strip(),
                area=case_area.strip(),
                mode=mode,  # type: ignore[arg-type]
                semantics=semantics,  # type: ignore[arg-type]
                inputs=tuple(inputs),
                development_exposed=bool(
                    row.get("development_exposed", any(item.development_exposed for item in inputs))
                ),
            )
        )
    return BenchmarkManifest(
        name=name.strip(),
        version=version.strip(),
        path=path,
        sha256=hashlib.sha256(raw).hexdigest(),
        cases=tuple(cases),
        uses_case_format=legacy_rows is None,
    )


def retry_delay_seconds(paper_id: str, attempt: int) -> int:
    """Return the fixed delay for any resumable benchmark checkpoint."""

    del paper_id, attempt
    return CHECKPOINT_RETRY_SECONDS


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
        wait_for_benchmark_output: Path | None = None,
        worker_services: tuple[str, ...] = (),
    ) -> None:
        if not 1 <= analysis_concurrency <= 4:
            raise ValueError("Analysis concurrency must be between 1 and 4")
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
        self.wait_for_benchmark_output = (
            wait_for_benchmark_output.resolve() if wait_for_benchmark_output else None
        )
        if self.wait_for_benchmark_output and not worker_services:
            raise ValueError("A benchmark dependency requires worker services to reload")
        if any(
            not re.fullmatch(r"paper-research-worker(?:-\d+)?\.service", service)
            for service in worker_services
        ):
            raise ValueError("Only Research Atlas analysis worker services may be reloaded")
        self.worker_services = worker_services
        self.state_path = self.output / "run-state.json"
        self.jobs_path = self.output / "jobs.json"
        self.state = self._load_or_initialize_state()
        self._children: dict[tuple[str, str], asyncio.subprocess.Process] = {}

    def _new_state(self) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        return {
            "schema_version": 2,
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
                "wait_for_benchmark_output": (
                    str(self.wait_for_benchmark_output)
                    if self.wait_for_benchmark_output
                    else None
                ),
                "worker_services": list(self.worker_services),
            },
            "papers": {
                case.id: {
                    "id": case.id,
                    "title": case.title,
                    "area": case.area,
                    "mode": case.mode,
                    "semantics": case.semantics,
                    "inputs": [
                        {
                            "id": paper.id,
                            "title": paper.title,
                            "area": paper.area,
                            "pdf": str(paper.path),
                            "sha256": paper.sha256,
                            "pages": paper.pages,
                            "development_exposed": paper.development_exposed,
                        }
                        for paper in case.inputs
                    ],
                    # Legacy fields keep the six-paper state/artifacts readable.
                    "pdf": str(case.inputs[0].path) if len(case.inputs) == 1 else None,
                    "sha256": case.inputs[0].sha256 if len(case.inputs) == 1 else None,
                    "pages": case.inputs[0].pages if len(case.inputs) == 1 else None,
                    "development_exposed": case.development_exposed,
                    "production": {
                        "status": "not_submitted",
                        "activation_required": case.mode == "multi",
                    },
                    "baseline": {
                        "status": "pending" if self.include_baseline else "skipped",
                        "attempts": 0,
                    },
                    "metrics": {"status": "pending", "attempts": 0},
                }
                for case in self.manifest.cases
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
            configured_dependency = (
                state.get("configuration", {}).get("wait_for_benchmark_output")
            )
            requested_dependency = (
                str(self.wait_for_benchmark_output)
                if self.wait_for_benchmark_output
                else None
            )
            if configured_dependency != requested_dependency:
                raise ValueError("The resumed run uses a different benchmark dependency")
            configuration = state.setdefault("configuration", {})
            configured_workers = list(configuration.get("worker_services", []))
            requested_workers = list(self.worker_services)
            # Scaling a live benchmark may append workers without invalidating
            # completed checkpoints. Removing or reordering workers remains an
            # error because the dependent joint run relies on a deterministic
            # rolling reload set.
            if configured_workers != requested_workers:
                if requested_workers[: len(configured_workers)] != configured_workers:
                    raise ValueError(
                        "The resumed run removes or reorders analysis worker services"
                    )
                configuration["worker_services"] = requested_workers
            configuration["analysis_concurrency"] = self.analysis_concurrency
            configuration["baseline_concurrency"] = self.baseline_concurrency
            configuration["judge_concurrency"] = self.judge_concurrency
            _atomic_json(self.state_path, state)
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

    async def _ensure_uploaded(
        self,
        case: BenchmarkCase,
        paper: BenchmarkPaper,
        owner_id: str,
        upload_state: dict[str, Any],
    ) -> str:
        upload_id = str(upload_state.get("upload_id") or uuid.uuid4())
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", paper.path.name)
        storage_path = str(
            upload_state.get("storage_path")
            or f"{owner_id}/{upload_id}/benchmark-{case.id}-{paper.id}-{safe_name}"
        )
        upload_state.update(
            {
                "status": "uploading",
                "upload_id": upload_id,
                "storage_path": storage_path,
            }
        )
        self._save()
        encoded_path = "/".join(quote(part, safe="") for part in storage_path.split("/"))
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
        upload_state["status"] = "uploaded"
        self._save()
        return upload_id

    async def _submit_one(self, case: BenchmarkCase, owner_id: str) -> None:
        entry = self.state["papers"][case.id]
        production = entry["production"]
        if production.get("job_id"):
            return
        if case.mode == "single":
            upload_id = await self._ensure_uploaded(
                case, case.inputs[0], owner_id, production
            )
            response = await self.repository._request(
                "POST",
                "/rest/v1/rpc/reserve_benchmark_job",
                json={
                    "p_owner_job_id": self.owner_job_id,
                    "p_upload_id": upload_id,
                    "p_benchmark_run_id": self.state["run_id"],
                    "p_paper_id": case.id,
                    "p_max_rounds": 1,
                    "p_languages": ["zh", "en"],
                },
            )
        else:
            upload_states = production.setdefault("uploads", {})
            upload_ids = []
            for paper in case.inputs:
                paper_state = upload_states.setdefault(paper.id, {})
                upload_ids.append(
                    await self._ensure_uploaded(case, paper, owner_id, paper_state)
                )
            response = await self.repository._request(
                "POST",
                "/rest/v1/rpc/reserve_benchmark_case_job",
                json={
                    "p_owner_job_id": self.owner_job_id,
                    "p_upload_ids": upload_ids,
                    "p_benchmark_run_id": self.state["run_id"],
                    "p_case_id": case.id,
                    "p_input_ids": [paper.id for paper in case.inputs],
                    "p_max_rounds": 1,
                    "p_languages": ["zh", "en"],
                    "p_initially_waiting": True,
                },
            )
        payload = response.json()
        row = payload[0] if isinstance(payload, list) else payload
        if not isinstance(row, dict) or not row.get("id"):
            raise RuntimeError(f"Benchmark reservation returned no job for {case.id}")
        production.update(
            {
                "job_id": str(row["id"]),
                "status": str(
                    row.get("status")
                    or ("waiting_resources" if case.mode == "multi" else "queued")
                ),
                "stage": str(row.get("stage") or "queued"),
                "progress": int(row.get("progress") or 0),
                "submitted_at": _iso(),
                "job_url": f"{self.site_url}#/jobs/{row['id']}",
            }
        )
        self._save()

    async def submit_cold_jobs(self) -> None:
        owner_id = await self._resolve_owner()
        for case in self.manifest.cases:
            await self._submit_one(case, owner_id)
        self.state["status"] = "running"
        self.state["submitted_at"] = self.state.get("submitted_at") or _iso()
        self._save()

    def _dependency_ready(self) -> bool:
        """Wait for every dependency job to complete and publish its report."""

        if self.wait_for_benchmark_output is None:
            return True
        dependency_path = self.wait_for_benchmark_output / "jobs.json"
        try:
            dependency = json.loads(dependency_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        jobs = dependency.get("jobs") if isinstance(dependency, dict) else None
        if not isinstance(jobs, list) or not jobs:
            return False
        return all(
            isinstance(row, dict)
            and row.get("job_status") == "completed"
            and bool(row.get("report_id"))
            for row in jobs
        )

    async def _analysis_slots_idle(self) -> bool:
        active = ",".join(sorted(ACTIVE_JOB_STATUSES))
        response = await self.repository._request(
            "GET",
            f"/rest/v1/jobs?status=in.({active})&select=id,benchmark_run_id&limit=100",
        )
        own_ids = {
            str(entry["production"].get("job_id"))
            for entry in self.state["papers"].values()
            if entry["production"].get("job_id")
        }
        return not any(
            str(row.get("id")) not in own_ids for row in (response.json() or [])
        )

    async def _reload_analysis_workers(self) -> None:
        if not self.worker_services:
            return
        reload_state = self.state.setdefault("worker_reload", {})
        if reload_state.get("completed"):
            return
        command = ["systemctl", "--user", "restart", *self.worker_services]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
        if process.returncode:
            diagnostic = redact((stderr or stdout).decode(errors="replace"))[-1200:]
            raise RuntimeError(f"analysis worker reload failed: {diagnostic}")
        for service in self.worker_services:
            check = await asyncio.create_subprocess_exec(
                "systemctl",
                "--user",
                "is-active",
                "--quiet",
                service,
            )
            return_code = await asyncio.wait_for(check.wait(), timeout=30)
            if return_code:
                raise RuntimeError(f"analysis worker did not become active: {service}")
        reload_state.update(
            {
                "completed": True,
                "services": list(self.worker_services),
                "completed_at": _iso(),
            }
        )
        self._save()

    async def _activate_waiting_cases(self) -> None:
        dependency_ready = self._dependency_ready()
        dependency = self.state.setdefault("dependency", {})
        dependency.update(
            {
                "path": (
                    str(self.wait_for_benchmark_output)
                    if self.wait_for_benchmark_output
                    else None
                ),
                "ready": dependency_ready,
                "checked_at": _iso(),
            }
        )
        if not dependency_ready:
            return
        slots_idle = await self._analysis_slots_idle()
        dependency["analysis_slots_idle"] = slots_idle
        if not slots_idle:
            return
        await self._reload_analysis_workers()
        for case in self.manifest.cases:
            production = self.state["papers"][case.id]["production"]
            if not production.get("activation_required") or production.get("activated_at"):
                continue
            response = await self.repository._request(
                "POST",
                "/rest/v1/rpc/activate_benchmark_case_job",
                json={
                    "p_benchmark_run_id": self.state["run_id"],
                    "p_case_id": case.id,
                },
            )
            payload = response.json()
            row = payload[0] if isinstance(payload, list) else payload
            if not isinstance(row, dict) or not row.get("id"):
                raise RuntimeError(f"Benchmark activation returned no job for {case.id}")
            production.update(
                {
                    "status": str(row.get("status") or "queued"),
                    "stage": str(row.get("stage") or "queued"),
                    "progress": int(row.get("progress") or 0),
                    "activated_at": _iso(),
                }
            )
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

    def _command_for(self, case: BenchmarkCase, kind: str) -> tuple[list[str], Path, str]:
        if kind == "baseline":
            output = self._baseline_output(case.id)
            command = [
                sys.executable,
                "-m",
                "paper_research.main",
                "baseline-local",
                *[str(paper.path) for paper in case.inputs],
                "--output",
                str(output),
            ]
            return command, output / "report.json", str(output)
        if kind != "metrics":
            raise ValueError(f"Unknown managed command kind: {kind}")
        production_report = self.output / "papers" / case.id / "production" / "report.json"
        baseline_report = self._baseline_output(case.id) / "report.json"
        output = self._metrics_output(case.id)
        command = [
            sys.executable,
            str(self.project_root / "benchmark" / "evaluate_teacher.py"),
            "--production-report",
            str(production_report),
            "--baseline-report",
            str(baseline_report),
            "--paper-id",
            case.id,
            *[
                value
                for paper in case.inputs
                for value in ("--pdf", str(paper.path), "--source-paper-id", paper.id)
            ],
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
        for case in self.manifest.cases:
            child = self._child_state(case.id, kind)
            if child.get("status") != "running":
                continue
            command, expected, marker = self._command_for(case, kind)
            del command
            key = (case.id, kind)
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
                delay = retry_delay_seconds(case.id, int(child.get("attempts") or 1))
                child["next_retry_at"] = _iso(_utcnow() + timedelta(seconds=delay))

    def _command_is_ready(self, case: BenchmarkCase, kind: str) -> bool:
        entry = self.state["papers"][case.id]
        if kind == "baseline":
            return self.include_baseline and (
                case.mode == "single"
                or bool(entry["production"].get("activated_at"))
            )
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
        for case in self.manifest.cases:
            if running >= concurrency:
                break
            child = self._child_state(case.id, kind)
            if child.get("status") in {"completed", "running", "incomplete", "skipped"}:
                continue
            if not self._command_is_ready(case, kind):
                continue
            due = _parse_time(child.get("next_retry_at"))
            if due and due > _utcnow():
                continue
            command, expected, marker = self._command_for(case, kind)
            if _managed_output_is_valid(kind, expected):
                child.update({"status": "completed", "completed_at": _iso()})
                continue
            log_path = self.output / "logs" / f"{case.id}-{kind}.log"
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
            self._children[(case.id, kind)] = process
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
                    "case_id": entry["id"],
                    "paper_id": entry["id"],
                    "title": entry["title"],
                    "mode": entry.get("mode", "single"),
                    "input_paper_ids": [
                        item.get("id") for item in entry.get("inputs", [])
                    ]
                    or [entry["id"]],
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
            "| Case | Production | Baseline | Metrics | Experiment | Result |",
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
        for case in self.manifest.cases:
            record = PaperMetricRecordProxy.model_validate_json(
                self._metrics_output(case.id).read_text(encoding="utf-8")
            )
            if record.paper_id != case.id:
                raise ValueError(
                    f"Metric artifact case id mismatch: expected {case.id}, got {record.paper_id}"
                )
            records.append(
                record.model_copy(update={"held_out": not case.development_exposed})
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
                    case.id for case in self.manifest.cases if case.development_exposed
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
                # Keep local baseline/metric work moving even when the remote
                # production-status poll is temporarily unavailable. Managed
                # subprocesses have their own durable checkpoints, so they do
                # not need to wait behind Supabase retry backoff.
                await self._manage_subprocesses()
                await self._activate_waiting_cases()
                await self._refresh_jobs_and_reports()
                await self._refresh_experiments()
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
                        "safe_error": redact(str(error) or type(error).__name__)[-2000:],
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
    wait_for_benchmark_output: Path | None = None,
    worker_services: tuple[str, ...] = (),
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
            wait_for_benchmark_output=wait_for_benchmark_output,
            worker_services=worker_services,
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
