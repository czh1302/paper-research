from paper_research.clients.llm import ClaudeCodeClient


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
    assert command[max_turns_index + 1] == "4"


def test_web_command_only_allows_web_search() -> None:
    client = ClaudeCodeClient("test")

    command = client._command("{}", client.cli_model, allow_web_search=True)

    tools_index = command.index("--tools")
    allowed_tools_index = command.index("--allowedTools")
    assert command[tools_index + 1] == "WebSearch"
    assert command[allowed_tools_index + 1] == "WebSearch"
    max_turns_index = command.index("--max-turns")
    assert command[max_turns_index + 1] == "8"
