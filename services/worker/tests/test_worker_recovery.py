from collections import defaultdict, deque
from pathlib import Path
from types import SimpleNamespace

from paper_research.pipeline import BudgetBlocked
from paper_research.worker import Worker


def recovery_worker() -> Worker:
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(JOB_RETRY_MAX_DELAY_SECONDS=21600)
    worker._failure_windows = defaultdict(deque)
    worker._circuit_open_until = {}
    return worker


def test_retry_schedule_is_fixed_until_circuit_breaker_opens() -> None:
    worker = recovery_worker()
    delays = [worker._retry_delay("job", attempt, "network") for attempt in range(6)]
    assert delays[:4] == [30, 30, 30, 30]
    assert delays[4] >= 599
    assert delays[5] >= 599

    budget_worker = recovery_worker()
    assert [
        budget_worker._retry_delay("job", attempt, "budget")
        for attempt in range(8)
    ] == [30] * 8


def test_failure_classification_and_invalid_pdf_detection() -> None:
    assert Worker._failure_category(BudgetBlocked("guard reached")) == "budget"
    assert Worker._failure_category(TimeoutError("request timeout")) == "network"
    assert Worker._needs_replacement_pdf(ValueError("Encrypted PDF requires password"))
    assert not Worker._needs_replacement_pdf(RuntimeError("HTTP 503"))


def test_database_enforces_fixed_retry_for_running_and_future_work() -> None:
    migration = (
        Path(__file__).parents[3]
        / "supabase/migrations/20260903020000_fixed_checkpoint_recovery.sql"
    ).read_text(encoding="utf-8")
    for table in (
        "public.jobs",
        "public.idea_experiments",
        "public.experiment_actions",
        "public.experiment_inference_requests",
        "public.experiment_runtime",
        "public.experiment_validation_runtime",
    ):
        assert table in migration
    assert "interval '30 seconds'" in migration
    assert "v_delay_seconds integer := 30" in migration
    assert "v_delay_seconds := 600" in migration
    assert "'infinity'::timestamptz" in migration
    assert "v_category = 'rate_limit'" in migration
