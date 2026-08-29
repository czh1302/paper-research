from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod

import httpx

from ..models import SearchQuery, SourceResult


class RateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self.interval = 1 / requests_per_second
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            delay = self.interval - (time.monotonic() - self._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request = time.monotonic()


class LiteratureSource(ABC):
    name: str

    def __init__(
        self,
        *,
        requests_per_second: float = 1,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.rate_limiter = RateLimiter(requests_per_second)
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=10), follow_redirects=True
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    @abstractmethod
    async def search(self, query: SearchQuery, limit: int = 10) -> SourceResult: ...
