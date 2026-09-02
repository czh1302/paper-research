#!/usr/bin/env python3
"""Low-cost E2B runtime smoke test for the Research Atlas template.

This test never invokes a language model or reads a paper. It verifies only the
provider primitives required by the experiment worker and always attempts to
destroy the temporary sandbox before returning.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from typing import Any

from e2b import PtySize
from paper_research.clients.e2b import E2BSandboxProvider
from paper_research.config import Settings


async def run_smoke(settings: Settings, template_id: str) -> dict[str, Any]:
    api_key = Settings.reveal(settings.E2B_API_KEY)
    if not api_key:
        raise RuntimeError("E2B_API_KEY is not configured")
    provider = E2BSandboxProvider(
        api_key,
        template_id=template_id,
        cpu_count=settings.E2B_CPU_COUNT,
        memory_mib=settings.E2B_MEMORY_MIB,
        disk_mib=settings.E2B_DISK_MIB,
        run_timeout_seconds=min(settings.E2B_RUN_TIMEOUT_SECONDS, 300),
    )
    handle = None
    sandbox_id = ""
    checks: dict[str, bool] = {}
    try:
        handle = await provider.create(
            experiment_id=f"smoke-{uuid.uuid4()}",
            allowed_hosts=["pypi.org"],
        )
        sandbox_id = handle.sandbox_id
        checks["created"] = True

        marker_path = "/home/user/repository/.research-atlas-smoke"
        await handle.write_text(marker_path, "research-atlas-e2b-smoke\n")
        checks["file_round_trip"] = (
            await handle.read_text(marker_path)
        ).strip() == "research-atlas-e2b-smoke"

        command = await handle.run(
            "python3 -c \"import json; print(json.dumps({'runtime': 'ok'}))\""
        )
        checks["command"] = command.exit_code == 0 and '"runtime": "ok"' in command.stdout

        output = bytearray()

        async def on_pty(data: bytes) -> None:
            output.extend(data)

        pty = await handle._sandbox.pty.create(  # noqa: SLF001 - provider smoke only
            PtySize(cols=80, rows=24),
            on_pty,
            cwd="/home/user/repository",
            timeout=30,
            request_timeout=15,
        )
        await handle._sandbox.pty.send_stdin(  # noqa: SLF001 - provider smoke only
            pty.pid,
            b"printf 'RESEARCH_ATLAS_PTY_OK\\n'; exit\n",
            request_timeout=15,
        )
        await pty.wait()
        checks["pty"] = b"RESEARCH_ATLAS_PTY_OK" in output

        allowed = await handle.run(
            "curl --silent --show-error --fail --max-time 15 --head https://pypi.org/simple/ >/dev/null"
        )
        checks["allow_list_permits_declared_host"] = allowed.exit_code == 0
        blocked = await handle.run(
            "curl --silent --show-error --fail --max-time 8 --head https://example.com >/dev/null",
            check=False,
            timeout=15,
        )
        checks["allow_list_blocks_other_host"] = blocked.exit_code != 0

        await handle.pause()
        checks["paused"] = True
        handle = await provider.connect(sandbox_id)
        checks["resumed"] = (
            await handle.read_text(marker_path)
        ).strip() == "research-atlas-e2b-smoke"
    finally:
        if sandbox_id:
            try:
                await provider.kill(sandbox_id)
                checks["destroyed"] = True
            except Exception:
                checks["destroyed"] = False

    failed = [name for name, passed in checks.items() if not passed]
    return {"ok": not failed, "checks": checks, "failed": failed}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the low-cost Research Atlas E2B smoke")
    parser.add_argument("--template", help="Override E2B_TEMPLATE_ID")
    args = parser.parse_args()
    settings = Settings()
    result = asyncio.run(run_smoke(settings, args.template or settings.E2B_TEMPLATE_ID))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
