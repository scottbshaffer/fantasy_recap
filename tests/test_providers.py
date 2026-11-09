import json
import subprocess
from types import SimpleNamespace

import pytest

from fantasy_recap import cli, providers
from fantasy_recap.config import DEFAULT_MODEL, StoryConfig
from fantasy_recap.espn import summarize_box_scores
from fantasy_recap.providers import ProviderError, generate, resolve_provider
from fantasy_recap.story import system_prompt, template_story, write_story


def week(points_box_scores):
    return summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")


def on_path(monkeypatch, *names):
    monkeypatch.setattr(providers, "_which", lambda name: f"/usr/bin/{name}" if name in names else None)


class FakeRun:
    """Stands in for subprocess.run and records the command it was given."""

    def __init__(self, stdout="", returncode=0, stderr="", write_file=None, raises=None):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr
        self.write_file, self.raises = write_file, raises
        self.cmd = self.kwargs = None

    def __call__(self, cmd, **kwargs):
        self.cmd, self.kwargs = cmd, kwargs
        if self.raises:
            raise self.raises
        if self.write_file is not None:
            out = cmd[cmd.index("--output-last-message") + 1]
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(self.write_file)
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


def claude_json(result="## Headline\nA fine recap.", **extra):
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": result,
                       "stop_reason": "end_turn", **extra})


# ---------------------------------------------------------------------------
# Choosing a provider


def test_auto_detect_order(monkeypatch):
    assert resolve_provider(StoryConfig()) == "template"
    on_path(monkeypatch, "codex")
    assert resolve_provider(StoryConfig()) == "codex"
    on_path(monkeypatch, "codex", "claude")
    assert resolve_provider(StoryConfig()) == "claude-code"
    assert resolve_provider(StoryConfig(openai_api_key="sk-o")) == "openai"
    assert resolve_provider(StoryConfig(openai_api_key="sk-o", api_key="sk-a")) == "anthropic"
    assert resolve_provider(StoryConfig(api_key="sk-a", base_url="http://localhost:11434/v1")) == "openai-compatible"


def test_explicit_provider_wins(monkeypatch):
    on_path(monkeypatch, "claude")
    assert resolve_provider(StoryConfig(api_key="sk-a", provider="codex")) == "codex"
    assert resolve_provider(StoryConfig(api_key="sk-a", provider="template")) == "template"


def test_provider_from_env(monkeypatch):
    monkeypatch.setenv("FANTASY_RECAP_PROVIDER", "Claude-Code")
    assert StoryConfig.from_env().provider == "claude-code"
    monkeypatch.setenv("FANTASY_RECAP_PROVIDER", "auto")
    assert StoryConfig.from_env().provider is None
    assert StoryConfig.from_env(provider="openai").provider == "openai"
    monkeypatch.setenv("FANTASY_RECAP_PROVIDER", "gemini")
    with pytest.raises(ValueError, match="FANTASY_RECAP_PROVIDER"):
        StoryConfig.from_env()


def test_env_settings(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-o")
    monkeypatch.setenv("FANTASY_RECAP_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("FANTASY_RECAP_CLAUDE_BIN", "/opt/claude")
    monkeypatch.setenv("FANTASY_RECAP_TIMEOUT", "90")
    c = StoryConfig.from_env()
    assert (c.openai_api_key, c.base_url, c.claude_bin, c.timeout) == ("sk-o", "https://openrouter.ai/api/v1",
                                                                      "/opt/claude", 90.0)
    # The OpenAI key is never sent to some other server.
    assert c.compatible_api_key is None
    monkeypatch.setenv("FANTASY_RECAP_API_KEY", "sk-or")
    assert StoryConfig.from_env().compatible_api_key == "sk-or"
    monkeypatch.setenv("FANTASY_RECAP_TIMEOUT", "soon")
    with pytest.raises(ValueError, match="FANTASY_RECAP_TIMEOUT"):
        StoryConfig.from_env()


def test_default_models():
    c = StoryConfig()
    assert c.model_for("anthropic") == c.model_for("claude-code") == DEFAULT_MODEL
    assert c.model_for("openai").startswith("gpt-")
    assert c.model_for("codex") is None
    assert c.model_for("openai-compatible") is None
    assert StoryConfig(model="llama3.1").model_for("openai-compatible") == "llama3.1"


# ---------------------------------------------------------------------------
# Claude Code


def test_claude_code_command_and_result(monkeypatch, points_box_scores):
    on_path(monkeypatch, "claude")
    run = FakeRun(stdout=claude_json())
    monkeypatch.setattr(providers.subprocess, "run", run)
    s = week(points_box_scores)
    story = write_story(s, StoryConfig(), include_award=False)
    assert story == "## Headline\nA fine recap."
    cmd = run.cmd
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--system-prompt") + 1] == system_prompt("basketball")
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--model") + 1] == DEFAULT_MODEL
    assert "--no-session-persistence" in cmd and "--strict-mcp-config" in cmd
    assert "--bare" not in cmd  # --bare ignores the Claude Code login
    # The league data goes in on stdin, and the CLI runs away from the repo.
    assert "Victor Wembanyama" in run.kwargs["input"]
    assert run.kwargs["cwd"] != "." and run.kwargs["timeout"] == 300.0


def test_claude_code_uses_login_not_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-a")
    monkeypatch.setenv("HOME", "/home/someone")
    on_path(monkeypatch, "claude")
    run = FakeRun(stdout=claude_json("ok"))
    monkeypatch.setattr(providers.subprocess, "run", run)
    generate("sys", "hi", StoryConfig.from_env(provider="claude-code"))
    assert "ANTHROPIC_API_KEY" not in run.kwargs["env"]
    assert run.kwargs["env"]["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
    assert run.kwargs["env"]["HOME"] == "/home/someone"


def test_claude_code_custom_bin_and_model(monkeypatch):
    on_path(monkeypatch, "/opt/claude")
    run = FakeRun(stdout=claude_json("ok"))
    monkeypatch.setattr(providers.subprocess, "run", run)
    assert generate("sys", "hi", StoryConfig(provider="claude-code", claude_bin="/opt/claude", model="opus")) == "ok"
    assert run.cmd[0] == "/opt/claude"
    assert run.cmd[run.cmd.index("--model") + 1] == "opus"


def test_claude_code_error(monkeypatch):
    on_path(monkeypatch, "claude")
    out = json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in"})
    monkeypatch.setattr(providers.subprocess, "run", FakeRun(stdout=out, returncode=1))
    with pytest.raises(ProviderError, match="Not logged in"):
        generate("sys", "hi", StoryConfig(provider="claude-code"))


def test_claude_code_error_subtype(monkeypatch):
    on_path(monkeypatch, "claude")
    out = json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                      "errors": ["rate limited"]})
    monkeypatch.setattr(providers.subprocess, "run", FakeRun(stdout=out, returncode=1))
    with pytest.raises(ProviderError, match="rate limited"):
        generate("sys", "hi", StoryConfig(provider="claude-code"))


def test_claude_code_garbage_output(monkeypatch):
    on_path(monkeypatch, "claude")
    monkeypatch.setattr(providers.subprocess, "run", FakeRun(stdout="", stderr="boom", returncode=2))
    with pytest.raises(ProviderError, match="exit 2.*boom"):
        generate("sys", "hi", StoryConfig(provider="claude-code"))


def test_claude_code_timeout(monkeypatch):
    on_path(monkeypatch, "claude")
    monkeypatch.setattr(providers.subprocess, "run", FakeRun(raises=subprocess.TimeoutExpired("claude", 5)))
    with pytest.raises(ProviderError, match="within 5 seconds"):
        generate("sys", "hi", StoryConfig(provider="claude-code", timeout=5))


def test_missing_cli():
    with pytest.raises(ProviderError, match="FANTASY_RECAP_CLAUDE_BIN"):
        generate("sys", "hi", StoryConfig(provider="claude-code"))
    with pytest.raises(ProviderError, match="FANTASY_RECAP_CODEX_BIN"):
        generate("sys", "hi", StoryConfig(provider="codex"))


# ---------------------------------------------------------------------------
# Codex


def test_codex_command_and_result(monkeypatch):
    on_path(monkeypatch, "codex")
    run = FakeRun(stdout="progress noise", write_file="  The recap.\n")
    monkeypatch.setattr(providers.subprocess, "run", run)
    assert generate("SYSTEM", "DATA", StoryConfig()) == "The recap."
    cmd = run.cmd
    assert cmd[:2] == ["codex", "exec"]
    assert cmd[-1] == "-"  # prompt read from stdin
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert "--skip-git-repo-check" in cmd and "--ephemeral" in cmd
    assert "--model" not in cmd  # Codex keeps its own default model
    assert run.kwargs["input"].startswith("SYSTEM") and run.kwargs["input"].endswith("DATA")


def test_codex_model_and_failure(monkeypatch):
    on_path(monkeypatch, "codex")
    run = FakeRun(returncode=1, stderr="Not signed in")
    monkeypatch.setattr(providers.subprocess, "run", run)
    with pytest.raises(ProviderError, match="Not signed in"):
        generate("s", "p", StoryConfig(provider="codex", model="gpt-6.1-sol"))
    assert run.cmd[run.cmd.index("--model") + 1] == "gpt-6.1-sol"


# ---------------------------------------------------------------------------
# OpenAI and OpenAI-compatible


class FakeCompletions:
    def __init__(self, content="An OpenAI recap.", finish_reason="stop"):
        self.content, self.finish_reason, self.kwargs = content, finish_reason, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=self.finish_reason)])


def fake_openai(**kw):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(**kw)))


def test_openai_request(points_box_scores):
    client = fake_openai()
    s = week(points_box_scores)
    story = write_story(s, StoryConfig(provider="openai", openai_api_key="sk-o"), client=client,
                        include_award=False)
    assert story == "An OpenAI recap."
    kwargs = client.chat.completions.kwargs
    assert kwargs["model"].startswith("gpt-")
    assert kwargs["max_completion_tokens"] > 0 and "max_tokens" not in kwargs
    assert kwargs["messages"][0] == {"role": "system", "content": system_prompt("basketball")}
    assert "Victor Wembanyama" in kwargs["messages"][1]["content"]


def test_openai_compatible_needs_model_and_skips_token_param():
    with pytest.raises(ProviderError, match="FANTASY_RECAP_MODEL"):
        generate("s", "p", StoryConfig(provider="openai-compatible", base_url="http://x"), client=fake_openai())
    client = fake_openai()
    generate("s", "p", StoryConfig(provider="openai-compatible", base_url="http://x", model="llama3.1"),
             client=client)
    kwargs = client.chat.completions.kwargs
    assert kwargs["model"] == "llama3.1" and "max_completion_tokens" not in kwargs


def test_openai_compatible_builds_client(monkeypatch):
    seen = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI, raising=False) if _has_openai() else \
        monkeypatch.setitem(__import__("sys").modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    generate("s", "p", StoryConfig(provider="openai-compatible", base_url="http://localhost:11434/v1",
                                   model="llama3.1"))
    assert seen == {"base_url": "http://localhost:11434/v1", "api_key": "not-needed"}


def _has_openai():
    try:
        import openai  # noqa: F401
    except ImportError:
        return False
    return True


def test_openai_truncated():
    with pytest.raises(ProviderError, match="output limit"):
        generate("s", "p", StoryConfig(provider="openai"), client=fake_openai(finish_reason="length"))


def test_empty_story_rejected():
    with pytest.raises(ProviderError, match="empty"):
        generate("s", "p", StoryConfig(provider="openai"), client=fake_openai(content="  "))


# ---------------------------------------------------------------------------
# CLI


def _fake_fetch(points_box_scores):
    def fetch(config, week=None, previous=False, big_games=10):
        return summarize_box_scores(points_box_scores, "Test League", config.year, week or 3, "H2H_POINTS")
    return fetch


def test_cli_provider_flag_uses_claude_code(monkeypatch, capsys, points_box_scores):
    monkeypatch.setenv("ESPN_LEAGUE_ID", "1")
    monkeypatch.setattr(cli, "fetch_week", _fake_fetch(points_box_scores))
    on_path(monkeypatch, "claude")
    monkeypatch.setattr(providers.subprocess, "run", FakeRun(stdout=claude_json("## Big week\nClaude Code wrote this.")))
    assert cli.main(["--week", "3", "--provider", "claude-code"]) == 0
    printed = capsys.readouterr()
    assert "Claude Code wrote this." in printed.out
    assert printed.err == ""


def test_cli_falls_back_to_template_when_cli_fails(monkeypatch, capsys, points_box_scores):
    monkeypatch.setenv("ESPN_LEAGUE_ID", "1")
    monkeypatch.setattr(cli, "fetch_week", _fake_fetch(points_box_scores))
    on_path(monkeypatch, "codex")
    monkeypatch.setattr(providers.subprocess, "run", FakeRun(returncode=1, stderr="Not signed in"))
    assert cli.main(["--week", "3"]) == 0
    printed = capsys.readouterr()
    assert "Codex couldn't write the story" in printed.err and "Not signed in" in printed.err
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    assert template_story(s).splitlines()[1] in printed.out


def test_cli_bad_provider_env(monkeypatch, capsys, points_box_scores):
    monkeypatch.setenv("ESPN_LEAGUE_ID", "1")
    monkeypatch.setenv("FANTASY_RECAP_PROVIDER", "gemini")
    monkeypatch.setattr(cli, "fetch_week", _fake_fetch(points_box_scores))
    assert cli.main(["--week", "3"]) == 2
    assert "FANTASY_RECAP_PROVIDER" in capsys.readouterr().err
