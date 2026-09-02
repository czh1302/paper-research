import pytest
from paper_research.models import (
    AnalysisMode,
    Job,
    JobFile,
    JobStatus,
    SubmissionIdeaSingleBatch,
)
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


def test_job_files_are_sorted_by_one_based_database_position() -> None:
    second = file(2).model_copy(update={"position": 2})
    first = file(1).model_copy(update={"position": 1})
    job = Job(
        id="job",
        user_id="user",
        mode=AnalysisMode.MULTI,
        max_rounds=1,
        status=JobStatus.QUEUED,
        files=[second, first],
    )

    assert [item.id for item in job.files] == ["file-1", "file-2"]

    with pytest.raises(ValidationError, match="contiguous from one"):
        Job(
            id="job",
            user_id="user",
            mode=AnalysisMode.MULTI,
            max_rounds=1,
            status=JobStatus.QUEUED,
            files=[first, second.model_copy(update={"position": 3})],
        )


def test_single_idea_batch_schema_limits_each_paid_call_to_one_idea() -> None:
    schema = SubmissionIdeaSingleBatch.model_json_schema()["properties"]["ideas"]

    assert schema["minItems"] == 1
    assert schema["maxItems"] == 1
