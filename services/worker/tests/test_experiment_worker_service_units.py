from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_primary_experiment_worker_uses_first_unique_id_and_eight_slots() -> None:
    unit = (ROOT / "ops/systemd/paper-research-experiment-worker.service").read_text(
        encoding="utf-8"
    )

    assert "EXPERIMENT_WORKER_ID=paper-experiment-worker-1" in unit
    assert "E2B_GLOBAL_CONCURRENCY=8" in unit


def test_experiment_worker_template_uses_instance_as_unique_id() -> None:
    unit = (ROOT / "ops/systemd/paper-research-experiment-worker@.service").read_text(
        encoding="utf-8"
    )

    assert "EXPERIMENT_WORKER_ID=paper-experiment-worker-%i" in unit
    assert "E2B_GLOBAL_CONCURRENCY=8" in unit
    assert "-m paper_research.main experiment-worker" in unit
