from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_secondary_analysis_worker_has_unique_lease_owner() -> None:
    unit = (ROOT / "ops/systemd/paper-research-worker-2.service").read_text(
        encoding="utf-8"
    )

    assert "EnvironmentFile=/home/czh/.config/paper-research/secrets.env" in unit
    assert "ExecStart=/usr/bin/env WORKER_ID=paper-worker-2 " in unit
    assert "-m paper_research.main worker" in unit
    assert "Restart=always" in unit


def test_installer_enables_four_analysis_and_eight_experiment_workers() -> None:
    installer = (ROOT / "scripts/install-worker-service.sh").read_text(
        encoding="utf-8"
    )

    assert "paper-research-worker.service" in installer
    assert "paper-research-worker-2.service" in installer
    assert "paper-research-worker-3.service" in installer
    assert "paper-research-worker-4.service" in installer
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
    assert "paper-research-experiment-worker.service" in health_check
    assert "paper-research-experiment-worker@${worker_number}.service" in health_check
    assert "EXPERIMENT_WORKER_ID" in health_check
    assert "--property=Environment" not in health_check
    assert "cat ${secrets_file}" not in health_check
