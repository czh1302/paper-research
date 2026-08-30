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
