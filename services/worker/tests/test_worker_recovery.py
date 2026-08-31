from collections import defaultdict, deque
from types import SimpleNamespace

from paper_research.pipeline import BudgetBlocked
from paper_research.worker import Worker


def recovery_worker() -> Worker:
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(JOB_RETRY_MAX_DELAY_SECONDS=21600)
    worker._failure_windows = defaultdict(deque)
    worker._circuit_open_until = {}
    return worker


def test_retry_schedule_uses_jittered_backoff_and_caps_at_six_hours() -> None:
    worker = recovery_worker()
    delays = [worker._retry_delay("job", attempt, "network") for attempt in range(6)]
    assert 24 <= delays[0] <= 36
    assert 96 <= delays[1] <= 144
    assert delays[-1] <= 21600


def test_failure_classification_and_invalid_pdf_detection() -> None:
    assert Worker._failure_category(BudgetBlocked("guard reached")) == "budget"
    assert Worker._failure_category(TimeoutError("request timeout")) == "network"
    assert Worker._needs_replacement_pdf(ValueError("Encrypted PDF requires password"))
    assert not Worker._needs_replacement_pdf(RuntimeError("HTTP 503"))
