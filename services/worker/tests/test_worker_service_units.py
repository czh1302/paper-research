from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("worker_number", range(2, 7))
def test_additional_analysis_worker_has_unique_lease_owner(worker_number: int) -> None:
    unit = (ROOT / f"ops/systemd/paper-research-worker-{worker_number}.service").read_text(
        encoding="utf-8"
    )

    assert "EnvironmentFile=/home/czh/.config/paper-research/secrets.env" in unit
    assert f"ExecStart=/usr/bin/env WORKER_ID=paper-worker-{worker_number} " in unit
    assert "-m paper_research.main worker" in unit
    assert "Restart=always" in unit


def test_installer_enables_six_analysis_and_eight_experiment_workers() -> None:
    installer = (ROOT / "scripts/install-worker-service.sh").read_text(
        encoding="utf-8"
    )

    assert "paper-research-worker.service" in installer
    assert "paper-research-worker-2.service" in installer
    assert "paper-research-worker-3.service" in installer
    assert "paper-research-worker-4.service" in installer
    assert "paper-research-worker-5.service" in installer
    assert "paper-research-worker-6.service" in installer
    assert "paper-research-experiment-worker@.service" in installer
    assert "for worker_number in {2..8}" in installer
    assert 'primary_worker_id' in installer
    assert 'secondary_worker_id="paper-worker-2"' in installer
    assert "disable --now paper-research-worker.service" not in installer
    assert "restart paper-research-worker.service" not in installer


def test_worker_health_check_never_dumps_service_environment() -> None:
    health_check = (ROOT / "scripts/check-worker-services.sh").read_text(
        encoding="utf-8"
    )

    assert "paper-research-worker.service" in health_check
    assert "paper-research-worker-2.service" in health_check
    assert "paper-research-worker-3.service" in health_check
    assert "paper-research-worker-4.service" in health_check
    assert "paper-research-worker-5.service" in health_check
    assert "paper-research-worker-6.service" in health_check
    assert "paper-research-experiment-worker.service" in health_check
    assert "paper-research-experiment-worker@${worker_number}.service" in health_check
    assert "EXPERIMENT_WORKER_ID" in health_check
    assert "--property=Environment" not in health_check
    assert "cat ${secrets_file}" not in health_check


def test_benchmark_services_use_all_six_analysis_workers() -> None:
    benchmark = (ROOT / "ops/systemd/paper-research-teacher-benchmark.service").read_text(
        encoding="utf-8"
    )
    joint = (
        ROOT / "ops/systemd/paper-research-teacher-joint-benchmark.service"
    ).read_text(encoding="utf-8")

    assert "--analysis-concurrency 6" in benchmark
    assert "--analysis-concurrency 6" in joint
    for worker_number in range(2, 7):
        assert f"paper-research-worker-{worker_number}.service" in benchmark
        assert f"--reload-worker-service paper-research-worker-{worker_number}.service" in joint
