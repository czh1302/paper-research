from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from paper_research.benchmark_metrics import PaperMetricRecordProxy
from paper_research.benchmark_runner import (
    BenchmarkManifestError,
    BenchmarkSupervisor,
    _json_file_is_valid,
    _managed_output_is_valid,
    load_benchmark_manifest,
    retry_delay_seconds,
)
from paper_research.main import build_parser
from paper_research.models import AnalysisMode, AnalysisReport, Job, JobFile, JobStatus
from paper_research.pipeline import AnalysisPipeline
from pypdf import PdfWriter

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "supabase/migrations/20260903000000_benchmark_jobs.sql"
JOINT_MIGRATION = ROOT / "supabase/migrations/20260903010000_joint_benchmark_cases.sql"


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
        if path == "/rest/v1/rpc/reserve_benchmark_case_job":
            case_id = kwargs["json"]["p_case_id"]
            return Response(
                {
                    "id": f"job-{case_id}",
                    "status": "waiting_resources",
                    "stage": "queued",
                    "progress": 0,
                }
            )
        if path == "/rest/v1/rpc/activate_benchmark_case_job":
            case_id = kwargs["json"]["p_case_id"]
            return Response(
                {
                    "id": f"job-{case_id}",
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


def _joint_manifest(tmp_path: Path) -> Path:
    inputs = []
    for index in range(2):
        pdf = tmp_path / f"joint-{index}.pdf"
        digest, pages = _write_pdf(pdf, f"joint-{index}")
        inputs.append(
            {
                "id": f"joint-paper-{index}",
                "title": f"Joint Paper {index}",
                "area": "systems",
                "local_path": pdf.name,
                "sha256": digest,
                "pages": pages,
                "development_exposed": index == 0,
            }
        )
    path = tmp_path / "joint-manifest.json"
    path.write_text(
        json.dumps(
            {
                "name": "teacher-joint",
                "version": "1",
                "cases": [
                    {
                        "id": "joint-case",
                        "title": "Symmetric joint case",
                        "area": "systems",
                        "mode": "multi",
                        "semantics": "symmetric",
                        "inputs": inputs,
                    }
                ],
            }
        ),
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


def test_case_manifest_preserves_symmetric_input_order(tmp_path: Path) -> None:
    manifest = load_benchmark_manifest(_joint_manifest(tmp_path), project_root=tmp_path)
    assert manifest.uses_case_format is True
    assert len(manifest.cases) == 1
    case = manifest.cases[0]
    assert case.mode == "multi"
    assert case.semantics == "symmetric"
    assert [paper.id for paper in case.inputs] == ["joint-paper-0", "joint-paper-1"]
    assert [paper.id for paper in manifest.papers] == [
        "joint-paper-0",
        "joint-paper-1",
    ]


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
    run_id = saved["run_id"]
    job_ids = {
        paper_id: row["production"]["job_id"]
        for paper_id, row in saved["papers"].items()
    }
    calls_before_resume = list(repository.calls)
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
    resumed_state = json.loads((output / "run-state.json").read_text(encoding="utf-8"))
    assert resumed_state["run_id"] == run_id
    assert {
        paper_id: row["production"]["job_id"]
        for paper_id, row in resumed_state["papers"].items()
    } == job_ids
    assert repository.calls == calls_before_resume


def test_resume_can_scale_analysis_and_append_worker_services(tmp_path: Path) -> None:
    manifest = load_benchmark_manifest(_manifest(tmp_path), project_root=tmp_path)
    output = tmp_path / "output"
    BenchmarkSupervisor(
        SimpleNamespace(),
        manifest,
        output,
        "owner-job",
        repository=SubmissionRepository(),
        project_root=tmp_path,
        analysis_concurrency=2,
        worker_services=("paper-research-worker.service",),
    )

    resumed = BenchmarkSupervisor(
        SimpleNamespace(),
        manifest,
        output,
        "owner-job",
        repository=SubmissionRepository(),
        project_root=tmp_path,
        resume=True,
        analysis_concurrency=6,
        judge_concurrency=4,
        worker_services=(
            "paper-research-worker.service",
            "paper-research-worker-2.service",
            "paper-research-worker-3.service",
            "paper-research-worker-4.service",
            "paper-research-worker-5.service",
            "paper-research-worker-6.service",
        ),
    )

    configuration = resumed.state["configuration"]
    assert configuration["analysis_concurrency"] == 6
    assert configuration["judge_concurrency"] == 4
    assert configuration["worker_services"][-1] == "paper-research-worker-6.service"

    with pytest.raises(ValueError, match="removes or reorders"):
        BenchmarkSupervisor(
            SimpleNamespace(),
            manifest,
            output,
            "owner-job",
            repository=SubmissionRepository(),
            project_root=tmp_path,
            resume=True,
            analysis_concurrency=6,
            worker_services=("paper-research-worker.service",),
        )


def test_analysis_concurrency_rejects_more_than_six(tmp_path: Path) -> None:
    manifest = load_benchmark_manifest(_manifest(tmp_path), project_root=tmp_path)
    with pytest.raises(ValueError, match="between 1 and 6"):
        BenchmarkSupervisor(
            SimpleNamespace(),
            manifest,
            tmp_path / "output",
            "owner-job",
            repository=SubmissionRepository(),
            project_root=tmp_path,
            analysis_concurrency=7,
        )


@pytest.mark.asyncio
async def test_joint_submission_uses_one_idempotent_ordered_case_reservation(
    tmp_path: Path,
) -> None:
    manifest = load_benchmark_manifest(_joint_manifest(tmp_path), project_root=tmp_path)
    repository = SubmissionRepository()
    output = tmp_path / "joint-output"
    supervisor = BenchmarkSupervisor(
        SimpleNamespace(),
        manifest,
        output,
        "owner-job",
        repository=repository,
        project_root=tmp_path,
    )
    await supervisor.submit_cold_jobs()

    reservations = [
        call
        for call in repository.calls
        if call[1] == "/rest/v1/rpc/reserve_benchmark_case_job"
    ]
    assert len(reservations) == 1
    payload = reservations[0][2]["json"]
    assert payload["p_case_id"] == "joint-case"
    assert payload["p_input_ids"] == ["joint-paper-0", "joint-paper-1"]
    assert len(payload["p_upload_ids"]) == 2
    assert payload["p_initially_waiting"] is True
    assert supervisor.state["papers"]["joint-case"]["production"]["status"] == (
        "waiting_resources"
    )

    calls_before_resume = list(repository.calls)
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
    assert repository.calls == calls_before_resume


def test_joint_baseline_and_evaluator_commands_keep_both_inputs_ordered(
    tmp_path: Path,
) -> None:
    manifest = load_benchmark_manifest(_joint_manifest(tmp_path), project_root=tmp_path)
    supervisor = BenchmarkSupervisor(
        SimpleNamespace(),
        manifest,
        tmp_path / "output",
        "owner-job",
        repository=SubmissionRepository(),
        project_root=tmp_path,
    )
    case = manifest.cases[0]
    assert supervisor._command_is_ready(case, "baseline") is False
    supervisor.state["papers"][case.id]["production"]["activated_at"] = (
        "2026-09-03T00:00:00+00:00"
    )
    assert supervisor._command_is_ready(case, "baseline") is True
    baseline, _, _ = supervisor._command_for(case, "baseline")
    first = baseline.index(str(case.inputs[0].path))
    second = baseline.index(str(case.inputs[1].path))
    assert first < second

    metrics, _, _ = supervisor._command_for(case, "metrics")
    assert [metrics[index + 1] for index, value in enumerate(metrics) if value == "--pdf"] == [
        str(case.inputs[0].path),
        str(case.inputs[1].path),
    ]
    assert [
        metrics[index + 1]
        for index, value in enumerate(metrics)
        if value == "--source-paper-id"
    ] == ["joint-paper-0", "joint-paper-1"]
    assert metrics[metrics.index("--repetitions") + 1] == "1"


@pytest.mark.asyncio
async def test_joint_activation_requires_dependency_idle_slots_and_worker_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ActivationRepository(SubmissionRepository):
        async def _request(self, method: str, path: str, **kwargs):
            if path.startswith("/rest/v1/jobs?status=in."):
                self.calls.append((method, path, kwargs))
                return Response([])
            return await super()._request(method, path, **kwargs)

    dependency = tmp_path / "teacher-v1"
    dependency.mkdir()
    (dependency / "jobs.json").write_text(
        json.dumps(
            {
                "jobs": [
                    {"job_status": "completed", "report_id": f"report-{index}"}
                    for index in range(6)
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = load_benchmark_manifest(_joint_manifest(tmp_path), project_root=tmp_path)
    repository = ActivationRepository()
    supervisor = BenchmarkSupervisor(
        SimpleNamespace(),
        manifest,
        tmp_path / "joint-output",
        "owner-job",
        repository=repository,
        project_root=tmp_path,
        wait_for_benchmark_output=dependency,
        worker_services=("paper-research-worker.service",),
    )
    await supervisor.submit_cold_jobs()

    blocked_rows = [
        {"job_status": "completed", "report_id": f"report-{index}"}
        for index in range(5)
    ] + [{"job_status": "needs_input", "report_id": None}]
    (dependency / "jobs.json").write_text(
        json.dumps({"jobs": blocked_rows}), encoding="utf-8"
    )
    assert supervisor._dependency_ready() is False
    (dependency / "jobs.json").write_text(
        json.dumps(
            {
                "jobs": [
                    {"job_status": "completed", "report_id": f"report-{index}"}
                    for index in range(6)
                ]
            }
        ),
        encoding="utf-8",
    )
    assert supervisor._dependency_ready() is True

    async def failed_reload() -> None:
        raise RuntimeError("reload failed")

    monkeypatch.setattr(supervisor, "_reload_analysis_workers", failed_reload)
    with pytest.raises(RuntimeError, match="reload failed"):
        await supervisor._activate_waiting_cases()
    assert not any(
        call[1] == "/rest/v1/rpc/activate_benchmark_case_job"
        for call in repository.calls
    )

    async def successful_reload() -> None:
        supervisor.state["worker_reload"] = {"completed": True}

    monkeypatch.setattr(supervisor, "_reload_analysis_workers", successful_reload)
    await supervisor._activate_waiting_cases()
    production = supervisor.state["papers"]["joint-case"]["production"]
    assert production["status"] == "queued"
    assert production.get("activated_at")


def test_retry_schedule_is_fixed_at_thirty_seconds() -> None:
    values = [retry_delay_seconds("paper", attempt) for attempt in range(1, 9)]
    assert values == [30] * 8


def test_json_output_validation_requires_a_valid_object(tmp_path: Path) -> None:
    valid = tmp_path / "valid.json"
    valid.write_text('{"report": true}', encoding="utf-8")
    assert _json_file_is_valid(valid) is True

    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    assert _json_file_is_valid(array) is False

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{", encoding="utf-8")
    assert _json_file_is_valid(corrupt) is False
    assert _json_file_is_valid(tmp_path / "missing.json") is False

    incomplete_report = tmp_path / "incomplete-report.json"
    incomplete_report.write_text("{}", encoding="utf-8")
    assert _managed_output_is_valid("baseline", incomplete_report) is False


@pytest.mark.asyncio
async def test_resume_reconciles_existing_baselines_without_relaunch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = load_benchmark_manifest(_manifest(tmp_path), project_root=tmp_path)
    supervisor = BenchmarkSupervisor(
        SimpleNamespace(),
        manifest,
        tmp_path / "output",
        "owner-job",
        repository=SubmissionRepository(),
        project_root=tmp_path,
    )
    completed_ids = [paper.id for paper in manifest.papers[:4]]
    for index, paper_id in enumerate(completed_ids):
        output = supervisor._baseline_output(paper_id)
        output.mkdir(parents=True, exist_ok=True)
        report = AnalysisReport(
            job_id=f"baseline-{paper_id}",
            problem_statements=[],
            related_papers=[],
            rounds=[],
            search_audit=[],
            source_coverage={},
            limitations_zh="有限",
            limitations_en="Limited",
        )
        (output / "report.json").write_text(
            report.model_dump_json(), encoding="utf-8"
        )
        child = supervisor.state["papers"][paper_id]["baseline"]
        child.update(
            {
                "status": "retrying" if index < 2 else "running",
                "attempts": 1,
                "pid": 999_999,
                "process_start_token": "missing",
            }
        )
    for paper in manifest.papers[4:]:
        supervisor.state["papers"][paper.id]["baseline"]["status"] = "skipped"

    async def unexpected_relaunch(*_args, **_kwargs):
        raise AssertionError("an existing valid baseline must not be relaunched")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_relaunch)
    await supervisor._inspect_managed_commands("baseline")
    await supervisor._launch_managed_commands("baseline", concurrency=4)

    for paper_id in completed_ids:
        child = supervisor.state["papers"][paper_id]["baseline"]
        assert child["status"] == "completed"
        assert child["attempts"] == 1


@pytest.mark.asyncio
async def test_live_managed_child_does_not_abort_supervisor_poll(
    tmp_path: Path,
) -> None:
    manifest = load_benchmark_manifest(_manifest(tmp_path), project_root=tmp_path)
    supervisor = BenchmarkSupervisor(
        SimpleNamespace(),
        manifest,
        tmp_path / "output",
        "owner-job",
        repository=SubmissionRepository(),
        project_root=tmp_path,
    )
    paper_id = manifest.papers[0].id
    child = supervisor.state["papers"][paper_id]["baseline"]
    child.update({"status": "running", "pid": 123, "process_start_token": "token"})

    class LiveProcess:
        returncode = None

        async def wait(self) -> int:
            await asyncio.sleep(1)
            return 0

    supervisor._children[(paper_id, "baseline")] = LiveProcess()  # type: ignore[assignment]

    await supervisor._inspect_managed_commands("baseline")

    assert child["status"] == "running"


def test_cli_exposes_parallel_benchmark_options() -> None:
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
            "6",
            "--baseline-concurrency",
            "2",
            "--judge-concurrency",
            "4",
            "--judge-repetitions",
            "1",
            "--resume",
        ]
    )
    assert args.command == "benchmark-run"
    assert args.cold and args.include_baseline and args.resume
    assert args.analysis_concurrency == 6
    assert args.baseline_concurrency == 2
    assert args.judge_concurrency == 4
    assert args.judge_repetitions == 1

    baseline_args = build_parser().parse_args(
        ["baseline-local", "first.pdf", "second.pdf", "--output", "out"]
    )
    assert baseline_args.files == [Path("first.pdf"), Path("second.pdf")]


def test_service_role_benchmark_reservation_is_atomic_and_idempotent() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "jobs_benchmark_run_paper_unique" in sql
    assert "reserve_benchmark_job" in sql
    assert "on conflict (benchmark_run_id, benchmark_paper_id)" in sql
    assert "insert into public.job_files" in sql
    assert "to service_role" in sql
    assert "from public, anon, authenticated" in sql

    joint_sql = JOINT_MIGRATION.read_text(encoding="utf-8")
    assert "reserve_benchmark_case_job" in joint_sql
    assert "activate_benchmark_case_job" in joint_sql
    assert "with ordinality" in joint_sql
    assert "p_initially_waiting" in joint_sql
    assert "'infinity'::timestamptz" in joint_sql
    assert "to service_role" in joint_sql
    assert "from public, anon, authenticated" in joint_sql


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
