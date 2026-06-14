import pytest
import subprocess

import commands
from commands import handle_command


class FakeStore:
    def __init__(self):
        self.model_calls = []
        self.effort_calls = []

    async def set_model(self, user_id, chat_id, model):
        self.model_calls.append((user_id, chat_id, model))

    async def set_effort(self, user_id, chat_id, effort):
        self.effort_calls.append((user_id, chat_id, effort))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("alias", "expected_model"),
    [
        ("opus", "claude-opus-4-8"),
        ("fable", "claude-fable-5"),
    ],
)
async def test_opus_and_fable_model_aliases_default_to_high_effort(alias, expected_model):
    store = FakeStore()

    reply = await handle_command("model", alias, "user_1", "chat_1", store)

    assert store.model_calls == [("user_1", "chat_1", expected_model)]
    assert store.effort_calls == [("user_1", "chat_1", "high")]
    assert expected_model in reply
    assert "high" in reply


@pytest.mark.asyncio
async def test_model_picker_shows_current_model_versions():
    class StoreWithCurrent(FakeStore):
        async def get_current(self, user_id, chat_id):
            return type("Session", (), {"model": "claude-sonnet-4-6"})()

    reply = await handle_command("model", "", "user_1", "chat_1", StoreWithCurrent())
    labels = [btn["text"] for btn in reply["buttons"]]

    assert labels == [
        "📚 Fable 5",
        "🧠 Opus 4.8",
        "⚡ Sonnet 4.6",
        "🐇 Haiku 4.5",
    ]
    assert any(btn["value"]["cmd"] == "/model fable" for btn in reply["buttons"])


@pytest.mark.asyncio
async def test_codex_model_picker_uses_codex_models(monkeypatch):
    monkeypatch.setattr(commands, "AGENT_BACKEND", "codex", raising=False)

    class StoreWithCurrent(FakeStore):
        async def get_current(self, user_id, chat_id):
            return type("Session", (), {"model": "gpt-5.5"})()

    reply = await handle_command("model", "", "user_1", "chat_1", StoreWithCurrent())
    labels = [btn["text"] for btn in reply["buttons"]]

    assert "Codex" in reply["text"]
    assert labels == ["🤖 GPT-5.5"]
    assert reply["buttons"][0]["value"]["cmd"] == "/model gpt-5.5"
    assert "Claude" not in "\n".join(labels)


@pytest.mark.asyncio
async def test_codex_model_aliases_do_not_map_to_claude(monkeypatch):
    monkeypatch.setattr(commands, "AGENT_BACKEND", "codex", raising=False)
    store = FakeStore()

    reply = await handle_command("model", "codex", "user_1", "chat_1", store)

    assert store.model_calls == [("user_1", "chat_1", "gpt-5.5")]
    assert store.effort_calls == []
    assert "gpt-5.5" in reply
    assert "claude" not in reply.lower()


def test_codex_mcp_list_uses_codex_cli(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="brave_search\nexa", stderr="")

    monkeypatch.setattr(commands, "AGENT_BACKEND", "codex", raising=False)
    monkeypatch.setattr(commands, "CODEX_CLI", "codex", raising=False)
    monkeypatch.setattr(subprocess, "run", fake_run)

    reply = commands._list_mcp()

    assert captured["cmd"] == ["codex", "mcp", "list"]
    assert "Codex" in reply
    assert "brave_search" in reply


@pytest.mark.asyncio
async def test_codex_help_is_backend_aware(monkeypatch):
    monkeypatch.setattr(commands, "AGENT_BACKEND", "codex", raising=False)

    reply = await handle_command("help", "", "user_1", "chat_1", FakeStore())

    assert "Codex" in reply
    assert "gpt-5.5" in reply
    assert "fable" not in reply.lower()
    assert "Claude Max" not in reply


@pytest.mark.asyncio
async def test_codex_think_defaults_to_high_effort(monkeypatch):
    monkeypatch.setattr(commands, "AGENT_BACKEND", "codex", raising=False)
    store = FakeStore()

    reply = await handle_command("think", "", "user_1", "chat_1", store)

    assert store.effort_calls == [("user_1", "chat_1", "high")]
    assert "high" in reply


@pytest.mark.asyncio
async def test_codex_fast_defaults_to_minimal_effort(monkeypatch):
    monkeypatch.setattr(commands, "AGENT_BACKEND", "codex", raising=False)
    store = FakeStore()

    reply = await handle_command("fast", "", "user_1", "chat_1", store)

    assert isinstance(reply, str)
    assert store.effort_calls == [("user_1", "chat_1", "minimal")]
    assert "minimal" in reply


@pytest.mark.asyncio
async def test_claude_fast_defaults_to_low_effort(monkeypatch):
    monkeypatch.setattr(commands, "AGENT_BACKEND", "claude", raising=False)
    store = FakeStore()

    reply = await handle_command("fast", "", "user_1", "chat_1", store)

    assert isinstance(reply, str)
    assert store.effort_calls == [("user_1", "chat_1", "low")]
    assert "low" in reply


def test_fast_is_bot_command_not_forwarded():
    assert "fast" in commands.BOT_COMMANDS


@pytest.mark.asyncio
async def test_codex_effort_menu_and_aliases_are_codex_safe(monkeypatch):
    monkeypatch.setattr(commands, "AGENT_BACKEND", "codex", raising=False)

    class StoreWithCurrent(FakeStore):
        async def get_current(self, user_id, chat_id):
            return type("Session", (), {"effort": "high"})()

    reply = await handle_command("effort", "", "user_1", "chat_1", StoreWithCurrent())
    labels = [btn["text"] for btn in reply["buttons"]]

    assert labels == ["🪶 Minimal", "⚡ Low", "⚖️ Medium", "🧠 High", "🤖 Auto"]
    assert "Max" not in "\n".join(labels)

    store = FakeStore()
    alias_reply = await handle_command("effort", "max", "user_1", "chat_1", store)
    assert store.effort_calls == [("user_1", "chat_1", "high")]
    assert "high" in alias_reply


def test_codex_usage_is_not_claude_oauth(monkeypatch):
    monkeypatch.setattr(commands, "AGENT_BACKEND", "codex", raising=False)

    reply = commands._get_usage()

    assert "Codex" in reply
    assert "Claude Code OAuth" not in reply
