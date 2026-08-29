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
