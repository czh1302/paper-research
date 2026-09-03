import asyncio
import json

import httpx
import pytest
import respx
from paper_research.clients.llm import (
    ClaudeCodeAccountingError,
    ClaudeCodeClient,
    ClaudeCodeError,
    ClaudeCodeStructuredOutputError,
    DeepSeekAPIClient,
)
from paper_research.models import ProviderUsage
from pydantic import BaseModel


class ExampleOutput(BaseModel):
    value: str


@respx.mock
async def test_direct_deepseek_structured_output_records_exact_usage() -> None:
    records: list[ProviderUsage] = []
    route = respx.post("https://api.deepseek.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "request-1",
                "choices": [{"message": {"content": '{"value":"complete"}'}}],
                "usage": {"prompt_tokens": 321, "completion_tokens": 123},
            },
        )
    )
    client = DeepSeekAPIClient(
        "test",
        usage_callback=records.append,
        strict_usage_callback=True,
    )

    result = await client.structured(
        "Return JSON for a repository plan.",
        ExampleOutput,
        stage="experiment_repository_manifest",
        usage_id="usage-1",
    )

    assert result.value == "complete"
    assert route.called
    request = json.loads(route.calls[0].request.content)
    assert request["model"] == "deepseek-v4-flash"
    assert request["response_format"] == {"type": "json_object"}
    assert request["thinking"] == {"type": "enabled"}
    assert len(records) == 1
    assert records[0].input_tokens == 321
    assert records[0].output_tokens == 123
    assert records[0].metadata["transport"] == "deepseek_api"
    assert records[0].metadata["experiment_usage_id"] == "usage-1"


@respx.mock
async def test_direct_deepseek_missing_usage_fails_closed() -> None:
    respx.post("https://api.deepseek.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"value":"complete"}'}}],
            },
        )
    )
    client = DeepSeekAPIClient("test", strict_usage_callback=True)

    with pytest.raises(ClaudeCodeAccountingError, match="no auditable provider usage"):
        await client.structured("Return JSON.", ExampleOutput)


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


def test_vision_model_uses_restricted_read_only_claude_code() -> None:
    client = ClaudeCodeClient("test")
    model = client._claude_cli_model("deepseek-v4-flash-vision-exp")
    command = client._command("{}", model, allow_web_search=False, allow_read=True)

    assert model == "deepseek-v4-flash-vision-exp"
    assert "--bare" in command
    assert "--restricted" in command
    assert command[command.index("--tools") + 1] == "Read"
    assert command[command.index("--allowedTools") + 1] == "Read"
    assert "WebSearch" not in command


def test_analysis_command_uses_supported_permission_mode_and_disables_tools() -> None:
    client = ClaudeCodeClient("test")

    command = client._command("{}", client.cli_model, allow_web_search=False)

    permission_index = command.index("--permission-mode")
    tools_index = command.index("--tools")
    assert command[permission_index + 1] == "dontAsk"
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


def test_experiment_command_has_process_budget_and_output_cap() -> None:
    client = ClaudeCodeClient("test", max_output_tokens=16_384)

    command = client._command(
        "{}",
        client.cli_model,
        allow_web_search=False,
        max_turns=4,
        max_budget_usd=0.25,
    )

    assert command[command.index("--max-turns") + 1] == "4"
    assert command[command.index("--max-budget-usd") + 1] == "0.250000"
    assert client._environment()["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "16384"


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


async def test_invalid_structured_output_preserves_raw_payload_for_repair(
    monkeypatch,
) -> None:
    raw = {"unexpected": "repair me"}

    class InvalidStructuredProcess:
        returncode = 0

        async def communicate(self, _prompt: bytes) -> tuple[bytes, bytes]:
            return json.dumps({"structured_output": raw}).encode(), b""

    async def create_process(*_args, **_kwargs):
        return InvalidStructuredProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    client = ClaudeCodeClient("test")

    with pytest.raises(ClaudeCodeStructuredOutputError) as caught:
        await client.structured("prompt", ExampleOutput)

    assert caught.value.structured_output == raw


async def test_experiment_usage_accounting_fails_closed() -> None:
    async def unavailable(_usage: ProviderUsage) -> None:
        raise RuntimeError("database temporarily unavailable")

    client = ClaudeCodeClient(
        "test",
        usage_callback=unavailable,
        strict_usage_callback=True,
    )

    with pytest.raises(ClaudeCodeError, match="durably accounted"):
        await client._emit_usage(
            {"usage": {"input_tokens": 100, "output_tokens": 20}},
            "deepseek-v4-flash",
            "claude-sonnet-4-5",
            "experiment_repository_design",
            failed=False,
        )


async def test_experiment_missing_usage_fails_closed() -> None:
    client = ClaudeCodeClient("test", strict_usage_callback=True)

    with pytest.raises(ClaudeCodeAccountingError, match="no auditable provider usage"):
        await client._emit_usage(
            {},
            "deepseek-v4-flash",
            "claude-sonnet-4-5",
            "experiment_repository_design",
            failed=False,
        )
