from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from paper_research.benchmark_metrics import PaperMetricRecordProxy
from paper_research.benchmark_runner import (
    BenchmarkManifestError,
    BenchmarkSupervisor,
    load_benchmark_manifest,
    retry_delay_seconds,
)
from paper_research.main import build_parser
from paper_research.models import AnalysisMode, AnalysisReport, Job, JobFile, JobStatus
from paper_research.pipeline import AnalysisPipeline
from pypdf import PdfWriter

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "supabase/migrations/20260903000000_benchmark_jobs.sql"


class Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class SubmissionRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def _request(self, method: str, path: str, **kwargs):
        self.calls.append((method, path, kwargs))
        if path.startswith("/rest/v1/jobs?"):
            return Response([{"id": "owner-job", "user_id": "owner-user"}])
        if path == "/rest/v1/rpc/reserve_benchmark_job":
            paper_id = kwargs["json"]["p_paper_id"]
            return Response(
                {
                    "id": f"job-{paper_id}",
                    "status": "queued",
                    "stage": "queued",
                    "progress": 0,
                }
            )
        return Response([])


def _write_pdf(path: Path, marker: str) -> tuple[str, int]:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.add_metadata({"/BenchmarkMarker": marker})
    with path.open("wb") as stream:
        writer.write(stream)
    return hashlib.sha256(path.read_bytes()).hexdigest(), 1


def _manifest(tmp_path: Path) -> Path:
    papers = []
    for index in range(6):
        pdf = tmp_path / f"paper-{index}.pdf"
        digest, pages = _write_pdf(pdf, str(index))
        papers.append(
            {
                "id": f"paper-{index}",
                "title": f"Paper {index}",
                "area": "systems",
                "local_path": pdf.name,
                "sha256": digest,
                "pages": pages,
                "development_exposed": index == 0,
            }
        )
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"name": "teacher", "version": "1", "papers": papers}),
        encoding="utf-8",
    )
    return path


def test_manifest_validates_all_six_pdfs_before_submission(tmp_path: Path) -> None:
    manifest = load_benchmark_manifest(_manifest(tmp_path), project_root=tmp_path)
    assert len(manifest.papers) == 6
    assert manifest.papers[0].development_exposed is True
    assert all(paper.pages == 1 for paper in manifest.papers)


def test_manifest_rejects_hash_drift(tmp_path: Path) -> None:
    path = _manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["papers"][2]["sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BenchmarkManifestError, match="SHA-256 mismatch"):
        load_benchmark_manifest(path, project_root=tmp_path)


@pytest.mark.asyncio
async def test_cold_submission_is_persisted_and_resume_is_idempotent(tmp_path: Path) -> None:
    manifest = load_benchmark_manifest(_manifest(tmp_path), project_root=tmp_path)
    repository = SubmissionRepository()
    output = tmp_path / "output"
    supervisor = BenchmarkSupervisor(
        SimpleNamespace(),
        manifest,
        output,
        "owner-job",
        repository=repository,
        resume=False,
        project_root=tmp_path,
    )
    await supervisor.submit_cold_jobs()

    reservations = [
        call for call in repository.calls if call[1] == "/rest/v1/rpc/reserve_benchmark_job"
    ]
    assert len(reservations) == 6
    saved = json.loads((output / "run-state.json").read_text(encoding="utf-8"))
    assert len({row["production"]["job_id"] for row in saved["papers"].values()}) == 6

    resumed = BenchmarkSupervisor(
        SimpleNamespace(),
        manifest,
        output,
        "owner-job",
        repository=repository,
        resume=True,
        project_root=tmp_path,
    )
    await resumed.submit_cold_jobs()
    assert len(
        [call for call in repository.calls if call[1] == "/rest/v1/rpc/reserve_benchmark_job"]
    ) == 6


def test_retry_schedule_has_stable_jitter_and_six_hour_cap() -> None:
    values = [retry_delay_seconds("paper", attempt) for attempt in range(1, 9)]
    assert values == [retry_delay_seconds("paper", attempt) for attempt in range(1, 9)]
    assert 24 <= values[0] <= 36
    assert all(value <= round(21600 * 1.2) for value in values)


def test_cli_exposes_frozen_parallel_benchmark_options() -> None:
    args = build_parser().parse_args(
        [
            "benchmark-run",
            "--manifest",
            "benchmark/teacher_benchmark_v1.json",
            "--owner-from-job",
            "08f0ca6d-abcf-42a4-9b58-6ed07996d135",
            "--cold",
            "--include-baseline",
            "--analysis-concurrency",
            "2",
            "--baseline-concurrency",
            "2",
            "--judge-concurrency",
            "2",
            "--resume",
        ]
    )
    assert args.command == "benchmark-run"
    assert args.cold and args.include_baseline and args.resume
    assert args.analysis_concurrency == 2


def test_service_role_benchmark_reservation_is_atomic_and_idempotent() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "jobs_benchmark_run_paper_unique" in sql
    assert "reserve_benchmark_job" in sql
    assert "on conflict (benchmark_run_id, benchmark_paper_id)" in sql
    assert "insert into public.job_files" in sql
    assert "to service_role" in sql
    assert "from public, anon, authenticated" in sql


def test_status_files_do_not_overwrite_metric_summary(tmp_path: Path) -> None:
    manifest = load_benchmark_manifest(_manifest(tmp_path), project_root=tmp_path)
    supervisor = BenchmarkSupervisor(
        SimpleNamespace(),
        manifest,
        tmp_path / "output",
        "owner-job",
        repository=SubmissionRepository(),
        project_root=tmp_path,
    )
    for paper in manifest.papers:
        supervisor.state["papers"][paper.id]["metrics"]["status"] = "completed"
        output = supervisor._metrics_output(paper.id)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            PaperMetricRecordProxy(
                paper_id=paper.id,
                held_out=not paper.development_exposed,
                scores_auto_proxy={"problem_correctness_auto": 0.5},
            ).model_dump_json(),
            encoding="utf-8",
        )
    supervisor._save()
    assert (supervisor.output / "status.md").is_file()
    assert (supervisor.output / "status.csv").is_file()
    assert not (supervisor.output / "summary.json").exists()

    supervisor._write_metric_summary()
    summary = json.loads((supervisor.output / "summary.json").read_text(encoding="utf-8"))
    assert summary["paper_count"] == 6
    assert summary["held_out_paper_count"] == 5


def test_completed_report_waits_for_automatic_main_experiment(tmp_path: Path) -> None:
    manifest = load_benchmark_manifest(_manifest(tmp_path), project_root=tmp_path)
    supervisor = BenchmarkSupervisor(
        SimpleNamespace(),
        manifest,
        tmp_path / "output",
        "owner-job",
        repository=SubmissionRepository(),
        project_root=tmp_path,
    )
    entry = next(iter(supervisor.state["papers"].values()))
    entry["production"].update(
        {"status": "completed", "report_id": "report", "experiment": {"status": "awaiting_enqueue"}}
    )
    entry["baseline"]["status"] = "completed"
    entry["metrics"]["status"] = "completed"
    assert supervisor._case_status(entry) == "RUNNING"


@pytest.mark.asyncio
async def test_baseline_reuses_completed_checkpoint_without_paid_work(tmp_path: Path) -> None:
    report = AnalysisReport(
        job_id="baseline-job",
        problem_statements=[],
        related_papers=[],
        rounds=[],
        search_audit=[],
        source_coverage={},
        limitations_zh="有限",
        limitations_en="Limited",
    )

    class Repository:
        async def load_pipeline_checkpoint(self, _job_id: str):
            return {"baseline": {"completed": True, "report": report.model_dump(mode="json")}}

    pipeline = AnalysisPipeline.__new__(AnalysisPipeline)
    pipeline.repository = Repository()
    pipeline.settings = SimpleNamespace(ARTIFACT_ROOT=tmp_path)
    pipeline._active_job_id = None

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("completed baseline must not parse or call a model")

    pipeline.parse_document = unexpected
    pipeline._call_llm = unexpected
    pdf = tmp_path / "paper.pdf"
    digest, _ = _write_pdf(pdf, "cached")
    job = Job(
        id="baseline-job",
        user_id="local",
        mode=AnalysisMode.SINGLE,
        max_rounds=1,
        status=JobStatus.QUEUED,
        files=[
            JobFile(
                id="upload",
                storage_path="local",
                original_name=pdf.name,
                size_bytes=pdf.stat().st_size,
                sha256=digest,
            )
        ],
    )
    result = await pipeline.analyze_baseline(job, pdf)
    assert result.job_id == "baseline-job"
