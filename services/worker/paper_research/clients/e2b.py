from __future__ import annotations

import asyncio
import shlex
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..experiment_models import CommandExecution

E2B_BASE_IMAGE_DIGEST = (
    "sha256:4a369f01a820fe5e65f53c2c5727a78899daf86f0541b721097f289559c8b73f"
)


class SandboxCommandError(RuntimeError):
    def __init__(self, execution: CommandExecution) -> None:
        super().__init__(
            f"Sandbox command exited with {execution.exit_code}: "
            f"{execution.stderr[-1000:] or execution.stdout[-1000:]}"
        )
        self.execution = execution


class SandboxNotFoundError(RuntimeError):
    """The provider confirmed that a previously persisted sandbox is gone."""


class SandboxFileTooLargeError(ValueError):
    """A sandbox file exceeded a caller-provided in-memory read limit."""


class SandboxRuntimeTaintedError(RuntimeError):
    """A command may still have descendants, so this runtime must be destroyed.

    The provider kill result is recorded for diagnostics only. The database
    lifecycle remains fail-closed in ``destroying`` until the reconciler has
    independently confirmed that the sandbox no longer exists.
    """

    def __init__(
        self,
        sandbox_id: str,
        *,
        destruction_requested: bool,
        cause: BaseException,
    ) -> None:
        super().__init__(
            "Sandbox command transport became unsafe; runtime destruction is required"
        )
        self.sandbox_id = sandbox_id
        self.destruction_requested = destruction_requested
        self.cause = cause


class SandboxHandle(Protocol):
    sandbox_id: str

    async def write_text(self, path: str, content: str) -> None: ...

    async def write_bytes(self, path: str, content: bytes) -> None: ...

    async def read_text(self, path: str) -> str: ...

    async def read_bytes(self, path: str) -> bytes: ...

    async def read_text_limited(self, path: str, max_bytes: int) -> str: ...

    async def read_bytes_limited(self, path: str, max_bytes: int) -> bytes: ...

    async def run(
        self,
        command: str,
        *,
        cwd: str = "/home/user/repository",
        timeout: int = 600,
        check: bool = True,
        on_output: Callable[[str, str], Awaitable[None] | None] | None = None,
    ) -> CommandExecution: ...

    async def pause(self) -> None: ...

    async def kill(self) -> None: ...


class SandboxProvider(Protocol):
    async def create(
        self,
        *,
        experiment_id: str,
        allowed_hosts: list[str],
        purpose: str = "interactive",
        tracking_id: str | None = None,
        on_created: Callable[[SandboxHandle], Awaitable[None]] | None = None,
    ) -> SandboxHandle: ...

    async def connect(self, sandbox_id: str) -> SandboxHandle: ...

    async def pause(self, sandbox_id: str) -> None: ...

    async def kill(self, sandbox_id: str) -> None: ...


@dataclass(slots=True)
class E2BSandboxHandle:
    _sandbox: Any
    sandbox_id: str

    async def write_text(self, path: str, content: str) -> None:
        await self._sandbox.files.write(path, content)

    async def write_bytes(self, path: str, content: bytes) -> None:
        await self._sandbox.files.write(path, content)

    async def read_text(self, path: str) -> str:
        return str(await self._sandbox.files.read(path, format="text"))

    async def read_bytes(self, path: str) -> bytes:
        return bytes(await self._sandbox.files.read(path, format="bytes"))

    async def read_bytes_limited(self, path: str, max_bytes: int) -> bytes:
        """Stream a remote file and stop before it can exhaust Worker memory."""
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        content = bytearray()
        stream = await self._sandbox.files.read(
            path,
            format="stream",
            request_timeout=120,
            stream_idle_timeout=30,
        )
        async with stream:
            async for chunk in stream:
                if len(content) + len(chunk) > max_bytes:
                    raise SandboxFileTooLargeError(
                        f"Sandbox file exceeds the {max_bytes}-byte read limit"
                    )
                content.extend(chunk)
        return bytes(content)

    async def read_text_limited(self, path: str, max_bytes: int) -> str:
        return (await self.read_bytes_limited(path, max_bytes)).decode("utf-8")

    async def run(
        self,
        command: str,
        *,
        cwd: str = "/home/user/repository",
        timeout: int = 600,
        check: bool = True,
        on_output: Callable[[str, str], Awaitable[None] | None] | None = None,
    ) -> CommandExecution:
        # The E2B 2.46 command handle accumulates every stdout/stderr chunk.
        # Execute generated code behind a root-owned supervisor so the untrusted
        # `user` process cannot forge the fixed provider marker through /proc or
        # replace capture files. The supervisor retains bounded byte tails and
        # tears down the process group. If the supervisor or transport times out,
        # the whole sandbox is destroyed so no detached child can keep running
        # while the Worker retries the same uncheckpointed step.
        capture_limit = 200_000
        token = uuid.uuid4().hex
        temp_root = f"/tmp/research-atlas-command-{token}"
        command_path = f"{temp_root}/command.sh"
        supervisor_path = f"{temp_root}/supervisor.py"
        stdout_path = f"{temp_root}/stdout.tail"
        stderr_path = f"{temp_root}/stderr.tail"
        command_script = f"#!/bin/bash\n{command}\n"
        supervisor_script = r'''import os
import pwd
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path


def user_processes(uid: int) -> set[tuple[int, str]]:
    processes = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            raw_stat = (entry / "stat").read_text(encoding="ascii")
            # Field 22 is process starttime. Split after the final ')' because
            # the command name may itself contain spaces or parentheses.
            starttime = raw_stat.rsplit(")", 1)[1].split()[19]
            processes.add((int(entry.name), starttime))
        except (FileNotFoundError, IndexError, OSError, ValueError):
            continue
    return processes


def kill_new_user_processes(uid: int, baseline: set[tuple[int, str]]) -> None:
    # Process groups do not contain setsid/double-fork descendants. Compare
    # PID+starttime identities against the pre-command snapshot and kill every
    # newly-created unprivileged process, repeating to close short fork races.
    for _attempt in range(4):
        remaining = user_processes(uid) - baseline
        if not remaining:
            return
        for pid, _starttime in remaining:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        time.sleep(0.05)


def append_tail(target: bytearray, chunk: bytes, limit: int) -> None:
    if len(chunk) >= limit:
        target[:] = chunk[-limit:]
        return
    overflow = len(target) + len(chunk) - limit
    if overflow > 0:
        del target[:overflow]
    target.extend(chunk)


command_path, stdout_path, stderr_path, cwd, raw_timeout, raw_limit = sys.argv[1:]
timeout = max(1, int(raw_timeout))
limit = max(1, int(raw_limit))
stdout_tail = bytearray()
stderr_tail = bytearray()
status = 190
timed_out = False
process = None
selector = selectors.DefaultSelector()
user_uid = pwd.getpwnam("user").pw_uid
baseline_user_processes = user_processes(user_uid)
try:
    command = Path(command_path).read_text(encoding="utf-8")
    process = subprocess.Popen(
        [
            "/usr/sbin/runuser",
            "-u",
            "user",
            "--",
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            command,
        ],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        close_fds=True,
    )
    assert process.stdout is not None and process.stderr is not None
    for stream, channel in ((process.stdout, "stdout"), (process.stderr, "stderr")):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, channel)
    deadline = time.monotonic() + timeout
    drain_deadline = None
    while selector.get_map() or process.poll() is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0 and not timed_out:
            timed_out = True
            drain_deadline = time.monotonic() + 5
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if drain_deadline is not None and time.monotonic() >= drain_deadline:
            break
        wait_for = 0.05 if timed_out else min(0.25, max(remaining, 0.01))
        for key, _events in selector.select(wait_for):
            try:
                chunk = os.read(key.fileobj.fileno(), 65536)
            except BlockingIOError:
                continue
            if not chunk:
                selector.unregister(key.fileobj)
                key.fileobj.close()
                continue
            append_tail(
                stdout_tail if key.data == "stdout" else stderr_tail,
                chunk,
                limit,
            )
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        status = process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        status = 124
    # Kill background descendants that stayed in the original process group.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if timed_out:
        status = 124
except BaseException as error:
    append_tail(stderr_tail, type(error).__name__.encode("ascii", "replace"), limit)
    if process is not None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
finally:
    kill_new_user_processes(user_uid, baseline_user_processes)
    selector.close()
    Path(stdout_path).write_bytes(stdout_tail)
    Path(stderr_path).write_bytes(stderr_tail)

print(f"__RESEARCH_ATLAS_EXIT__={status}")
'''
        await self._sandbox.commands.run(
            f"/bin/mkdir -m 700 -- {shlex.quote(temp_root)}",
            cwd="/home/user",
            timeout=30,
            user="root",
        )
        await self._sandbox.files.write(command_path, command_script, user="root")
        await self._sandbox.files.write(supervisor_path, supervisor_script, user="root")
        await self._sandbox.commands.run(
            "/bin/chown root:root -- "
            f"{shlex.quote(command_path)} {shlex.quote(supervisor_path)} && "
            f"/bin/chmod 400 -- {shlex.quote(command_path)} && "
            f"/bin/chmod 500 -- {shlex.quote(supervisor_path)}",
            cwd="/home/user",
            timeout=30,
            user="root",
        )
        wrapper = (
            f"/usr/bin/python3 -I -S {shlex.quote(supervisor_path)} "
            f"{shlex.quote(command_path)} {shlex.quote(stdout_path)} "
            f"{shlex.quote(stderr_path)} {shlex.quote(cwd)} {max(1, timeout)} "
            f"{capture_limit}"
        )
        started = time.monotonic()
        try:
            result = await self._sandbox.commands.run(
                wrapper,
                cwd="/home/user",
                timeout=max(31, timeout + 30),
                user="root",
            )
            marker = str(getattr(result, "stdout", "")).strip()
            prefix = "__RESEARCH_ATLAS_EXIT__="
            if len(marker.splitlines()) != 1 or not marker.startswith(prefix):
                raise RuntimeError("Sandbox command wrapper returned an invalid status")
            exit_code = int(marker.removeprefix(prefix).splitlines()[-1])
        except BaseException as error:
            # The provider stream ended without a trustworthy supervisor
            # result. Destroying the sandbox is intentionally fail-closed: it
            # prevents a child command from surviving a lease loss/timeout and
            # mutating the repository while a recovered Worker repeats a step.
            destruction_requested = False
            try:
                await asyncio.shield(self._sandbox.kill())
                destruction_requested = True
            except BaseException:
                pass
            raise SandboxRuntimeTaintedError(
                self.sandbox_id,
                destruction_requested=destruction_requested,
                cause=error,
            ) from error
        try:
            stdout = (
                await self.read_bytes_limited(stdout_path, capture_limit)
            ).decode("utf-8", errors="replace")
        except Exception:
            stdout = ""
        try:
            stderr = (
                await self.read_bytes_limited(stderr_path, capture_limit)
            ).decode("utf-8", errors="replace")
        except Exception:
            stderr = ""
        if on_output:
            for channel, captured in (("stdout", stdout), ("stderr", stderr)):
                if not captured:
                    continue
                callback_result = on_output(channel, captured)
                if asyncio.iscoroutine(callback_result):
                    await callback_result
        if exit_code == 124:
            timeout_error = TimeoutError(
                "Sandbox command exceeded its execution timeout"
            )
            destruction_requested = False
            try:
                await self._sandbox.kill()
                destruction_requested = True
            except BaseException:
                pass
            raise SandboxRuntimeTaintedError(
                self.sandbox_id,
                destruction_requested=destruction_requested,
                cause=timeout_error,
            ) from timeout_error
        # Cleanup is a separate root-only, fixed-output provider command.
        try:
            await self._sandbox.commands.run(
                f"/bin/rm -rf -- {shlex.quote(temp_root)}",
                cwd="/home/user",
                timeout=30,
                user="root",
            )
        except Exception:
            pass
        execution = CommandExecution(
            command=command,
            exit_code=exit_code,
            stdout=stdout[-200_000:],
            stderr=stderr[-200_000:],
            elapsed_seconds=round(time.monotonic() - started, 3),
        )
        if check and exit_code != 0:
            raise SandboxCommandError(execution)
        return execution

    async def pause(self) -> None:
        await self._sandbox.pause(keep_memory=True)

    async def kill(self) -> None:
        await self._sandbox.kill()


class E2BSandboxProvider:
    """Thin, injectable E2B Python 2.46.0 adapter.

    CPU/RAM are fixed by the prebuilt template. The provider verifies the
    resulting sandbox before any untrusted generated code is executed.
    """

    PACKAGE_HOSTS = (
        "pypi.org",
        "*.pypi.org",
        "files.pythonhosted.org",
        "registry.npmjs.org",
        "github.com",
        "*.github.com",
        "githubusercontent.com",
        "*.githubusercontent.com",
        "huggingface.co",
        "*.huggingface.co",
    )

    def __init__(
        self,
        api_key: str,
        *,
        template_id: str,
        cpu_count: int,
        memory_mib: int,
        disk_mib: int,
        run_timeout_seconds: int,
    ) -> None:
        self.api_key = api_key
        self.template_id = template_id
        self.cpu_count = cpu_count
        self.memory_mib = memory_mib
        self.disk_mib = disk_mib
        self.run_timeout_seconds = run_timeout_seconds

    @staticmethod
    def _sdk() -> Any:
        try:
            from e2b import AsyncSandbox
        except ImportError as error:  # pragma: no cover - installation diagnostic
            raise RuntimeError("e2b==2.46.0 is required by experiment-worker") from error
        return AsyncSandbox

    async def create(
        self,
        *,
        experiment_id: str,
        allowed_hosts: list[str],
        purpose: str = "interactive",
        tracking_id: str | None = None,
        on_created: Callable[[SandboxHandle], Awaitable[None]] | None = None,
    ) -> E2BSandboxHandle:
        # The second, evaluator-only phase receives every dependency from the
        # pinned template and every scientific input as a server-verified file.
        # It therefore has no reason to access the network. This prevents a
        # frozen evaluator (or a compromised raw artifact parser) from turning
        # an evaluation into an exfiltration channel.
        hosts = (
            []
            if purpose == "formal_evaluator"
            else list(dict.fromkeys([*self.PACKAGE_HOSTS, *allowed_hosts]))
        )
        sandbox_cls = self._sdk()
        metadata = {
            "product": "research-atlas",
            "experiment_id": experiment_id,
            "runtime_purpose": purpose,
            "template_id": self.template_id,
            "base_image_digest": E2B_BASE_IMAGE_DIGEST,
        }
        if tracking_id:
            metadata["tracking_id"] = tracking_id
        sandbox = await sandbox_cls.create(
            template=self.template_id,
            timeout=self.run_timeout_seconds,
            metadata=metadata,
            secure=True,
            allow_internet_access=bool(hosts),
            network={
                "allow_out": hosts,
                # E2B exposes ALL_TRAFFIC as the concrete 0.0.0.0/0 selector.
                "deny_out": ["0.0.0.0/0"],
                "allow_public_traffic": False,
            },
            lifecycle={"on_timeout": "pause", "auto_resume": True},
            api_key=self.api_key,
        )
        handle = E2BSandboxHandle(sandbox, str(sandbox.sandbox_id))
        # The caller persists the external ID before any verification call can
        # fail. This closes the otherwise untrackable post-create orphan
        # window and lets the lifecycle reconciler finish an ambiguous kill.
        if on_created is not None:
            await on_created(handle)
        await self._verify_resources(sandbox)
        return handle

    async def _verify_resources(self, sandbox: Any) -> None:
        info = await sandbox.get_info()
        actual_cpu = int(getattr(info, "cpu_count", 0) or 0)
        actual_memory = int(getattr(info, "memory_mb", 0) or 0)
        if actual_cpu and actual_cpu < self.cpu_count:
            await sandbox.kill()
            raise RuntimeError(
                f"E2B template exposes {actual_cpu} vCPU; expected {self.cpu_count}"
            )
        if actual_memory and actual_memory < self.memory_mib:
            await sandbox.kill()
            raise RuntimeError(
                f"E2B template exposes {actual_memory} MiB; expected {self.memory_mib}"
            )
        disk = await sandbox.commands.run(
            "df -Pm /home/user | awk 'NR==2 {print $2}'", timeout=30
        )
        try:
            actual_disk = int(str(getattr(disk, "stdout", "")).strip())
        except ValueError:
            await sandbox.kill()
            raise RuntimeError("Could not verify E2B sandbox disk capacity") from None
        # Filesystem metadata consumes a small part of the advertised 10 GiB.
        if actual_disk < round(self.disk_mib * 0.95):
            await sandbox.kill()
            raise RuntimeError(
                f"E2B sandbox exposes {actual_disk} MiB disk; expected {self.disk_mib}"
            )

    async def connect(self, sandbox_id: str) -> E2BSandboxHandle:
        try:
            sandbox = await self._sdk().connect(
                sandbox_id,
                timeout=self.run_timeout_seconds,
                api_key=self.api_key,
            )
        except Exception as error:
            from e2b import SandboxNotFoundException

            if isinstance(error, SandboxNotFoundException):
                raise SandboxNotFoundError(sandbox_id) from error
            raise
        return E2BSandboxHandle(sandbox, str(sandbox.sandbox_id))

    async def pause(self, sandbox_id: str) -> None:
        try:
            await self._sdk().pause(sandbox_id, keep_memory=True, api_key=self.api_key)
        except Exception as error:
            from e2b import SandboxNotFoundException

            if isinstance(error, SandboxNotFoundException):
                raise SandboxNotFoundError(sandbox_id) from error
            raise

    async def kill(self, sandbox_id: str) -> None:
        try:
            await self._sdk().kill(sandbox_id, api_key=self.api_key)
        except Exception as error:
            from e2b import SandboxNotFoundException

            if isinstance(error, SandboxNotFoundException):
                raise SandboxNotFoundError(sandbox_id) from error
            raise
