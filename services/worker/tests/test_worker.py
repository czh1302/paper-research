import asyncio
from types import SimpleNamespace

import paper_research.worker as worker_module
import pytest
from paper_research.worker import Worker


@pytest.mark.asyncio
async def test_heartbeat_retries_after_transient_repository_error(monkeypatch) -> None:
    class Repository:
        calls = 0

        async def renew_lease(self, *_args) -> None:
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("temporary timeout")
            raise asyncio.CancelledError

    async def immediate_sleep(_seconds: float) -> None:
        return None

    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(JOB_LEASE_SECONDS=90, WORKER_ID="worker-1")
    worker.repository = Repository()
    monkeypatch.setattr(worker_module.asyncio, "sleep", immediate_sleep)

    with pytest.raises(asyncio.CancelledError):
        await worker._heartbeat("job-1")

    assert worker.repository.calls == 2


@pytest.mark.asyncio
async def test_admin_job_deletion_waits_for_safe_lease_then_cleans() -> None:
    class Repository:
        ready = False
        deleted: list[str] = []
        finished: list[dict] = []

        async def claim_admin_deletion_request(self, *_args):
            return {"id": "request-1", "target_kind": "job", "target_id": "job-1", "attempt_count": 1}

        async def admin_deletion_target_ready(self, *_args):
            return self.ready

        async def delete_job_permanently(self, job_id: str):
            self.deleted.append(job_id)

        async def finish_admin_deletion_request(self, _request_id, _worker_id, **values):
            self.finished.append(values)

    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(WORKER_ID="worker-1", JOB_LEASE_SECONDS=300)
    worker.repository = Repository()

    await worker._process_admin_deletion()
    assert worker.repository.deleted == []
    assert worker.repository.finished[-1]["success"] is False

    worker.repository.ready = True
    await worker._process_admin_deletion()
    assert worker.repository.deleted == ["job-1"]
    assert worker.repository.finished[-1]["success"] is True
