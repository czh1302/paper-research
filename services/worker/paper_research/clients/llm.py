from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from ..models import ProviderUsage
from ..security import redact

SchemaModel = TypeVar("SchemaModel", bound=BaseModel)


class ClaudeCodeError(RuntimeError):
    pass


class ClaudeCodeClient:
    def __init__(
        self,
        api_key: str,
        *,
        binary: str = "claude",
        model: str = "deepseek-v4-flash",
        cli_model: str | None = None,
        effort: str = "high",
        timeout_seconds: int = 900,
        usage_callback: Callable[[ProviderUsage], Any] | None = None,
    ) -> None:
        self.api_key = api_key
        self.binary = binary
        self.model = model
        self.cli_model = cli_model or self._claude_cli_model(model)
        self.effort = effort
        self.timeout_seconds = timeout_seconds
        self.usage_callback = usage_callback

    @staticmethod
    def _claude_cli_model(provider_model: str) -> str:
        """Return a Claude model name that DeepSeek's compatibility layer can map.

        DeepSeek maps Claude Opus names to V4 Pro and Claude Sonnet/Haiku names to
        V4 Flash. Passing a DeepSeek model name directly works for some requests,
        but fails for Claude Code's internal requests (for example session titles).
        """
        normalized = provider_model.casefold()
        if normalized.startswith("claude-"):
            return provider_model
        if "v4-pro" in normalized or normalized == "opus":
            return "claude-opus-4-5"
        return "claude-sonnet-4-5"

    def _environment(self, cli_model: str | None = None) -> dict[str, str]:
        selected_cli_model = cli_model or self.cli_model
        environment = os.environ.copy()
        environment.update(
            {
                "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
                "ANTHROPIC_AUTH_TOKEN": self.api_key,
                "ANTHROPIC_MODEL": selected_cli_model,
                "ANTHROPIC_DEFAULT_OPUS_MODEL": selected_cli_model,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": selected_cli_model,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": selected_cli_model,
                "CLAUDE_CODE_SUBAGENT_MODEL": selected_cli_model,
                "CLAUDE_CODE_EFFORT_LEVEL": self.effort,
                "CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_TELEMETRY": "1",
                "DISABLE_ERROR_REPORTING": "1",
                "DISABLE_BUG_COMMAND": "1",
                "DISABLE_AUTOUPDATER": "1",
            }
        )
        return environment

    async def structured(
        self,
        prompt: str,
        response_model: type[SchemaModel],
        *,
        allow_web_search: bool = False,
        model: str | None = None,
    ) -> SchemaModel:
        provider_model = model or self.model
        cli_model = self.cli_model if model is None else self._claude_cli_model(provider_model)
        schema = json.dumps(
            response_model.model_json_schema(), ensure_ascii=False, separators=(",", ":")
        )
        command = [
            self.binary,
            "--safe-mode",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            schema,
            "--model",
            cli_model,
            "--effort",
            self.effort,
            "--max-turns",
            "4" if allow_web_search else "1",
            "--no-session-persistence",
            "--disable-slash-commands",
            "--permission-mode",
            "dontAsk",
        ]
        if allow_web_search:
            command.extend(["--tools", "WebSearch", "--allowedTools", "WebSearch"])
        else:
            command.extend(["--tools", ""])

        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._environment(cli_model),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode("utf-8")), timeout=self.timeout_seconds
            )
        except TimeoutError:
            process.terminate()
            await process.wait()
            raise ClaudeCodeError("Claude Code invocation timed out") from None

        if process.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            diagnostic = stderr_text or stdout_text or "no diagnostic output"
            error = redact(diagnostic)[-4000:]
            raise ClaudeCodeError(f"Claude Code exited with {process.returncode}: {error}")
        try:
            payload = json.loads(stdout)
            structured = payload.get("structured_output")
            if structured is None:
                result = payload.get("result")
                structured = json.loads(result) if isinstance(result, str) else result
            parsed = response_model.model_validate(structured)
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise ClaudeCodeError(
                f"Claude Code returned invalid structured output: {error}"
            ) from error

        usage = payload.get("usage") or {}
        usage_record = ProviderUsage(
            provider="deepseek",
            model=provider_model,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            metadata={"client_cost_usd": payload.get("total_cost_usd")},
        )
        if self.usage_callback:
            callback_result = self.usage_callback(usage_record)
            if asyncio.iscoroutine(callback_result):
                await callback_result
        return parsed
