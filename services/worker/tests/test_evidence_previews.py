import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from paper_research.evidence_previews import backfill_benchmark_evidence_previews
from paper_research.main import build_parser

RUN_ID = "d76fceac-9afc-4ce7-9e89-fd596aa95cbc"


class PreviewRepository:
    def __init__(self, *, completed: bool = True) -> None:
        self.completed = completed
        self.generated: list[tuple[str, int]] = []
        self.events: list[tuple[str, str, dict[str, object]]] = []

    async def list_benchmark_report_jobs(self, benchmark_run_id: str):
        assert benchmark_run_id == RUN_ID
        return [
            {
                "id": "job-1",
                "status": "completed" if self.completed else "rendering",
                "benchmark_paper_id": "paper-1",
                "benchmark_case_id": None,
                "report": {"id": "report-1"} if self.completed else None,
            },
            {
                "id": "job-2",
                "status": "completed",
                "benchmark_paper_id": "paper-2",
                "benchmark_case_id": None,
                "report": {"id": "report-2"},
            },
        ]

    async def generate_evidence_previews(
        self, job_id: str, workspace: Path, *, concurrency: int
    ) -> int:
        assert workspace.is_dir()
        self.generated.append((job_id, concurrency))
        return 3 if len(self.generated) <= 2 else 0

    async def add_event(
        self, job_id: str, kind: str, message: str, data: dict[str, object]
    ) -> None:
        del message
        self.events.append((job_id, kind, data))


@pytest.mark.asyncio
async def test_benchmark_preview_backfill_is_resumable_and_idempotent(tmp_path: Path) -> None:
    repository = PreviewRepository()
    settings = SimpleNamespace(ARTIFACT_ROOT=tmp_path)

    result = await backfill_benchmark_evidence_previews(
        settings,
        benchmark_run_id=RUN_ID,
        concurrency=2,
        resume=True,
        output=tmp_path,
        repository=repository,
    )
    assert result == 0
    assert repository.generated == [("job-1", 2), ("job-2", 2)]
    assert [event[1] for event in repository.events] == [
        "evidence_previews",
        "evidence_previews",
    ]

    result = await backfill_benchmark_evidence_previews(
        settings,
        benchmark_run_id=RUN_ID,
        concurrency=2,
        resume=True,
        output=tmp_path,
        repository=repository,
    )
    assert result == 0
    state = json.loads((tmp_path / RUN_ID / "run-state.json").read_text())
    assert state["status"] == "completed"
    assert state["jobs"]["job-1"]["new_previews"] == 3
    assert state["jobs"]["job-2"]["new_previews"] == 3


@pytest.mark.asyncio
async def test_benchmark_preview_backfill_rejects_unfinished_jobs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="completed with a report"):
        await backfill_benchmark_evidence_previews(
            SimpleNamespace(ARTIFACT_ROOT=tmp_path),
            benchmark_run_id=RUN_ID,
            output=tmp_path,
            repository=PreviewRepository(completed=False),
        )


def test_cli_exposes_evidence_preview_backfill_options() -> None:
    args = build_parser().parse_args(
        [
            "evidence-preview-backfill",
            "--benchmark-run",
            RUN_ID,
            "--concurrency",
            "2",
            "--resume",
        ]
    )
    assert args.command == "evidence-preview-backfill"
    assert args.benchmark_run == RUN_ID
    assert args.concurrency == 2
    assert args.resume
