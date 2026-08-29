import pytest
from paper_research.models import AnalysisMode, Job, JobFile, JobStatus
from pydantic import ValidationError


def file(number: int) -> JobFile:
    return JobFile(
        id=f"file-{number}",
        storage_path=f"user/file-{number}/paper.pdf",
        original_name="paper.pdf",
        size_bytes=100,
    )


def test_single_mode_requires_one_file() -> None:
    with pytest.raises(ValidationError):
        Job(
            id="job",
            user_id="user",
            mode=AnalysisMode.SINGLE,
            max_rounds=1,
            status=JobStatus.QUEUED,
            files=[file(1), file(2)],
        )


def test_multi_mode_accepts_two_to_five_files() -> None:
    job = Job(
        id="job",
        user_id="user",
        mode=AnalysisMode.MULTI,
        max_rounds=5,
        status=JobStatus.QUEUED,
        files=[file(1), file(2)],
    )
    assert len(job.files) == 2
