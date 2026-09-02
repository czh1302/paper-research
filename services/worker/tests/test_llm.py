import asyncio
import json

import pytest
from paper_research.clients.llm import ClaudeCodeClient, ClaudeCodeError
from paper_research.models import ProviderUsage
from pydantic import BaseModel


class ExampleOutput(BaseModel):
    value: str


def test_deepseek_models_use_claude_aliases() -> None:
    flash = ClaudeCodeClient("test", model="deepseek-v4-flash")
    pro = ClaudeCodeClient("test", model="deepseek-v4-pro")

    assert flash.cli_model == "claude-sonnet-4-5"
    assert pro.cli_model == "claude-opus-4-5"
    assert flash._environment()["ANTHROPIC_MODEL"] == "claude-sonnet-4-5"
    assert pro._environment()["ANTHROPIC_MODEL"] == "claude-opus-4-5"


def test_explicit_claude_model_is_preserved() -> None:
    client = ClaudeCodeClient("test", model="claude-sonnet-4-5")

    assert client.cli_model == "claude-sonnet-4-5"


def test_analysis_command_uses_supported_permission_mode_and_disables_tools() -> None:
    client = ClaudeCodeClient("test")

    command = client._command("{}", client.cli_model, allow_web_search=False)

    permission_index = command.index("--permission-mode")
    tools_index = command.index("--tools")
    assert command[permission_index + 1] == "default"
    assert command[tools_index + 1] == ""
    assert "--allowedTools" not in command
    assert "--safe-mode" in command
    assert "--strict-mcp-config" in command
    max_turns_index = command.index("--max-turns")
    assert command[max_turns_index + 1] == "8"


def test_web_command_only_allows_web_search() -> None:
    client = ClaudeCodeClient("test")

    command = client._command("{}", client.cli_model, allow_web_search=True)

    tools_index = command.index("--tools")
    allowed_tools_index = command.index("--allowedTools")
    assert command[tools_index + 1] == "WebSearch"
    assert command[allowed_tools_index + 1] == "WebSearch"
    max_turns_index = command.index("--max-turns")
    assert command[max_turns_index + 1] == "12"


def test_max_turns_can_be_tuned_for_long_structured_calls() -> None:
    client = ClaudeCodeClient("test", analysis_max_turns=10, web_max_turns=14)

    analysis = client._command("{}", client.cli_model, allow_web_search=False)
    web = client._command("{}", client.cli_model, allow_web_search=True)

    assert analysis[analysis.index("--max-turns") + 1] == "10"
    assert web[web.index("--max-turns") + 1] == "14"


async def test_failed_cli_result_still_records_token_usage(monkeypatch) -> None:
    records: list[ProviderUsage] = []

    class FailedProcess:
        returncode = 1

        async def communicate(self, _prompt: bytes) -> tuple[bytes, bytes]:
            return (
                json.dumps(
                    {
                        "subtype": "error_max_turns",
                        "usage": {"input_tokens": 1200, "output_tokens": 300},
                        "total_cost_usd": 99,
                    }
                ).encode(),
                b"",
            )

    async def create_process(*_args, **_kwargs):
        return FailedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    client = ClaudeCodeClient("test", usage_callback=records.append)

    with pytest.raises(ClaudeCodeError, match="exited with 1"):
        await client.structured(
            "prompt",
            ExampleOutput,
            model="deepseek-v4-pro",
            stage="v4_idea_review",
        )

    assert len(records) == 1
    assert records[0].input_tokens == 1200
    assert records[0].output_tokens == 300
    assert records[0].model == "deepseek-v4-pro"
    assert records[0].metadata["failed"] is True
    assert records[0].metadata["subtype"] == "error_max_turns"
    assert records[0].metadata["transport"] == "claude_code"
    assert records[0].metadata["stage"] == "v4_idea_review"
    assert records[0].metadata["claude_cli_model"] == "claude-opus-4-5"
