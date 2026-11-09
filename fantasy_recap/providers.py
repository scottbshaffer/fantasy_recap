"""Story-writer backends: who turns the prompt into a recap.

Each backend takes a system prompt and a user prompt and returns the model's text.

- anthropic          Claude through the Anthropic API (ANTHROPIC_API_KEY)
- claude-code        the `claude` CLI in print mode, using your existing Claude Code login (no key)
- codex              the `codex` CLI (`codex exec`), using your existing Codex / ChatGPT login
- openai             the OpenAI API (OPENAI_API_KEY)
- openai-compatible  any server that speaks the OpenAI Chat Completions API, given a base URL
                     (OpenRouter, Ollama, LM Studio, vLLM, Groq, Together...)
- template           no model at all; the built-in template story

FANTASY_RECAP_PROVIDER picks one. Left unset, the first that's available wins, in this order:
a base URL, an Anthropic key, an OpenAI key, the `claude` CLI, the `codex` CLI, the template.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any, Optional

from .config import STORY_PROVIDERS as PROVIDERS, StoryConfig

LABELS = {
    "anthropic": "Claude",
    "claude-code": "Claude Code",
    "codex": "Codex",
    "openai": "OpenAI",
    "openai-compatible": "The model server",
    "template": "The template",
}

# Output limit for Claude, well above the ~400-word story (extended thinking isn't turned on).
MAX_OUTPUT_TOKENS = 16000
# OpenAI's current models reason before they write, and that reasoning counts against this limit.
# OpenAI suggests leaving about 25k tokens, so this is set higher than the story alone needs.
OPENAI_MAX_COMPLETION_TOKENS = 32000


class ProviderError(RuntimeError):
    """A backend couldn't produce a story."""


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


def resolve_provider(config: StoryConfig) -> str:
    """The backend to use: FANTASY_RECAP_PROVIDER when set, otherwise the first one available."""
    if config.provider:
        return config.provider
    if config.base_url:
        return "openai-compatible"
    if config.api_key:
        return "anthropic"
    if config.openai_api_key:
        return "openai"
    if _which(config.claude_bin):
        return "claude-code"
    if _which(config.codex_bin):
        return "codex"
    return "template"


def generate(system: str, prompt: str, config: StoryConfig, provider: Optional[str] = None,
             client: Any = None) -> str:
    """Run one backend and return its text. `client` lets tests stand in for an API SDK client."""
    provider = provider or resolve_provider(config)
    if provider == "anthropic":
        text = _anthropic(system, prompt, config, client)
    elif provider in ("openai", "openai-compatible"):
        text = _openai(system, prompt, config, provider, client)
    elif provider == "claude-code":
        text = _claude_code(system, prompt, config)
    elif provider == "codex":
        text = _codex(system, prompt, config)
    else:
        raise ProviderError(f"{provider!r} doesn't use a model.")
    text = (text or "").strip()
    if not text:
        raise ProviderError("The model returned an empty story.")
    return text


# ---------------------------------------------------------------------------
# API backends


def _anthropic(system: str, prompt: str, config: StoryConfig, client: Any) -> str:
    if client is None:
        if not config.api_key:
            raise ProviderError("ANTHROPIC_API_KEY isn't set.")
        import anthropic

        client = anthropic.Anthropic(api_key=config.api_key)
    response = client.messages.create(
        model=config.model_for("anthropic"),
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "max_tokens":
        raise ProviderError("The model hit max_tokens before finishing the story.")
    return "".join(block.text for block in response.content if block.type == "text")


def _openai(system: str, prompt: str, config: StoryConfig, provider: str, client: Any) -> str:
    model = config.model_for(provider)
    if not model:
        raise ProviderError("Set FANTASY_RECAP_MODEL to the model id your server should use.")
    if client is None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError("the openai package isn't installed (pip install openai).") from exc
        if provider == "openai":
            if not config.openai_api_key:
                raise ProviderError("OPENAI_API_KEY isn't set.")
            client = OpenAI(api_key=config.openai_api_key)
        else:
            if not config.base_url:
                raise ProviderError("FANTASY_RECAP_BASE_URL isn't set.")
            # Local servers like Ollama ignore the key, but the SDK insists on one.
            client = OpenAI(base_url=config.base_url, api_key=config.compatible_api_key or "not-needed")
    kwargs: dict = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }
    if provider == "openai":
        # Newer OpenAI models reject max_tokens. Other servers vary, so they keep their own default.
        kwargs["max_completion_tokens"] = OPENAI_MAX_COMPLETION_TOKENS
    response = client.chat.completions.create(**kwargs)
    if not response.choices:
        raise ProviderError("The model returned no choices.")
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise ProviderError("The model hit its output limit before finishing the story.")
    return choice.message.content or ""


# ---------------------------------------------------------------------------
# CLI backends


def _run(cmd: list[str], prompt: str, config: StoryConfig, name: str, bin_var: str,
         drop_env: tuple[str, ...] = (), extra_env: Optional[dict] = None) -> subprocess.CompletedProcess:
    """Run a CLI with the prompt on stdin, from a scratch directory so no project files get picked up."""
    if not _which(cmd[0]):
        raise ProviderError(f"the `{cmd[0]}` command wasn't found. Install {name} or set {bin_var} to its path.")
    with tempfile.TemporaryDirectory(prefix="fantasy-recap-") as scratch:
        try:
            env = {k: v for k, v in os.environ.items() if k not in drop_env}
            env.update(extra_env or {})
            return subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=scratch,
                                  timeout=config.timeout, env=env)
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"{name} didn't finish within {config.timeout:g} seconds.") from exc
        except OSError as exc:
            raise ProviderError(f"couldn't run {name}: {exc}") from exc


def _tail(text: str, limit: int = 300) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def _claude_code(system: str, prompt: str, config: StoryConfig) -> str:
    # Print mode with no tools: a single completion on the user's own Claude Code login.
    # Don't add --bare: it only accepts ANTHROPIC_API_KEY and would skip the login.
    cmd = [
        config.claude_bin, "-p",
        "--output-format", "json",
        "--system-prompt", system,
        "--tools", "",
        "--strict-mcp-config",  # don't start the user's MCP servers for a one-shot story
        "--no-session-persistence",
    ]
    model = config.model_for("claude-code")
    if model:
        cmd += ["--model", model]
    # Claude Code prefers ANTHROPIC_API_KEY over the login when both exist. Choosing claude-code means
    # "use my login", so the key (often loaded from .env for the anthropic provider) is held back.
    # CLAUDE.md memory files are coding notes, not recap instructions, so they're left out too.
    proc = _run(cmd, prompt, config, "Claude Code", "FANTASY_RECAP_CLAUDE_BIN", drop_env=("ANTHROPIC_API_KEY",),
                extra_env={"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"})
    try:
        result = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        result = None
    if not isinstance(result, dict):
        raise ProviderError(f"Claude Code failed (exit {proc.returncode}): "
                            f"{_tail(proc.stderr or proc.stdout) or 'no output'}")
    if proc.returncode != 0 or result.get("is_error") or result.get("subtype", "success") != "success":
        errors = result.get("errors")
        detail = (result.get("result") or ("; ".join(map(str, errors)) if errors else "")
                  or _tail(proc.stderr) or result.get("subtype") or "unknown error")
        raise ProviderError(f"Claude Code failed: {detail}")
    if result.get("stop_reason") == "max_tokens":
        raise ProviderError("The model hit max_tokens before finishing the story.")
    return result.get("result") or ""


def _codex(system: str, prompt: str, config: StoryConfig) -> str:
    # `codex exec` is Codex's non-interactive mode. It reads the prompt from stdin when given "-",
    # and -o writes just the final message to a file, so its progress output doesn't matter.
    # Codex has no plain system-prompt flag, so the instructions simply lead the prompt.
    with tempfile.TemporaryDirectory(prefix="fantasy-recap-codex-") as out_dir:
        out_file = os.path.join(out_dir, "story.txt")
        cmd = [
            config.codex_bin, "exec",
            "--skip-git-repo-check",
            "--sandbox", "read-only",
            "--ephemeral",
            "--output-last-message", out_file,
        ]
        model = config.model_for("codex")
        if model:
            cmd += ["--model", model]
        cmd.append("-")
        proc = _run(cmd, f"{system}\n\n---\n\n{prompt}", config, "Codex", "FANTASY_RECAP_CODEX_BIN")
        if proc.returncode != 0:
            raise ProviderError(f"Codex failed (exit {proc.returncode}): "
                                f"{_tail(proc.stderr or proc.stdout) or 'no output'}")
        try:
            with open(out_file, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            # Older versions without -o: the final message is what's printed to stdout.
            return proc.stdout
