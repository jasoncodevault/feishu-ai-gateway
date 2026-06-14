import asyncio
import os
import sys

import pytest

os.environ.setdefault("FEISHU_APP_ID", "test_app_id")
os.environ.setdefault("FEISHU_APP_SECRET", "test_app_secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import claude_runner
from claude_runner import run_claude


class FakeStdin:
    def __init__(self):
        self.buffer = b""
        self.closed = False

    def write(self, data: bytes):
        self.buffer += data

    async def drain(self):
        return None

    def close(self):
        self.closed = True


class FakeStdout:
    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)
        self._index = 0

    async def readline(self):
        if self._index >= len(self._lines):
            return b""
        line = self._lines[self._index]
        self._index += 1
        return line


class FakeStderr:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self):
        return self._data


class FakeProc:
    def __init__(self, stdout_lines: list[bytes], stderr: bytes = b"", returncode: int = 0):
        self.stdin = FakeStdin()
        self.stdout = FakeStdout(stdout_lines)
        self.stderr = FakeStderr(stderr)
        self.returncode = returncode

    async def wait(self):
        return self.returncode

    def kill(self):
        pass


def test_run_claude_prefers_final_result_over_partial_deltas(monkeypatch):
    proc = FakeProc([
        b'{"type":"system","session_id":"sid_123"}\n',
        b'{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}}\n',
        b'{"type":"result","session_id":"sid_123","result":"Hello world"}\n',
    ])

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    text, session_id, used_fallback = asyncio.run(run_claude("hi"))

    assert text == "Hello world"
    assert session_id == "sid_123"
    assert used_fallback is False
    assert proc.stdin.buffer.endswith(b"hi\n")
    assert proc.stdin.closed is True


def test_run_claude_returns_partial_output_on_nonzero_exit_with_stderr(monkeypatch):
    """When there's partial output + stderr + nonzero exit, return partial text (don't raise)"""
    proc = FakeProc([
        b'{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"partial"}}}\n',
    ], stderr=b"boom", returncode=1)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    text, session_id, used_fallback = asyncio.run(run_claude("hi"))
    assert text == "partial"
    assert used_fallback is False


def test_run_claude_raises_on_nonzero_exit_without_output(monkeypatch):
    """When there's NO output and nonzero exit, raise RuntimeError"""
    proc = FakeProc([], stderr=b"fatal error", returncode=1)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(RuntimeError, match=r"fatal error"):
        asyncio.run(run_claude("hi"))


def test_run_claude_retries_without_resume_on_empty_stderr_failure(monkeypatch):
    first = FakeProc([], stderr=b"", returncode=1)
    second = FakeProc([
        b'{"type":"system","session_id":"sid_new"}\n',
        b'{"type":"result","session_id":"sid_new","result":"fresh answer"}\n',
    ])
    procs = iter([first, second])

    async def fake_create_subprocess_exec(*args, **kwargs):
        return next(procs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    text, session_id, used_fallback = asyncio.run(run_claude("hi", session_id="sid_old"))

    assert text == "fresh answer"
    assert session_id == "sid_new"
    assert used_fallback is True
    assert first.stdin.closed is True
    assert second.stdin.closed is True


def test_run_claude_streams_text_chunks_via_callback(monkeypatch):
    """Test that on_text_chunk callback fires for text deltas"""
    proc = FakeProc([
        b'{"type":"system","session_id":"sid_1"}\n',
        b'{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello "}}}\n',
        b'{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"world"}}}\n',
        b'{"type":"result","session_id":"sid_1","result":"Hello world"}\n',
    ])

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    chunks = []

    async def collect_chunk(chunk):
        chunks.append(chunk)

    text, session_id, _ = asyncio.run(
        run_claude("hi", on_text_chunk=collect_chunk)
    )

    assert chunks == ["Hello ", "world"]
    assert text == "Hello world"


def test_run_claude_fires_tool_use_callback(monkeypatch):
    """Test that on_tool_use callback fires for tool calls"""
    proc = FakeProc([
        b'{"type":"system","session_id":"sid_1"}\n',
        b'{"type":"stream_event","event":{"type":"content_block_start","content_block":{"type":"tool_use","name":"Bash"}}}\n',
        b'{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"{\\"command\\": \\"ls\\"}"}}}\n',
        b'{"type":"stream_event","event":{"type":"content_block_stop"}}\n',
        b'{"type":"result","session_id":"sid_1","result":"done"}\n',
    ])

    async def fake_create_subprocess_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    tool_calls = []

    async def collect_tool(name, inp):
        tool_calls.append((name, inp))

    text, _, _ = asyncio.run(
        run_claude("hi", on_tool_use=collect_tool)
    )

    # Should fire twice: once on block_start (empty input), once on block_stop (full input)
    assert len(tool_calls) == 2
    assert tool_calls[0] == ("Bash", {})
    assert tool_calls[1] == ("Bash", {"command": "ls"})


def test_run_claude_codex_backend_starts_exec_and_parses_json(monkeypatch):
    proc = FakeProc([
        b'{"type":"thread.started","thread_id":"thread_123"}\n',
        b'{"type":"item.started","item":{"type":"command_execution","command":"/bin/bash -lc pwd"}}\n',
        b'{"type":"item.completed","item":{"type":"agent_message","text":"Codex answer"}}\n',
    ])
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(claude_runner, "AGENT_BACKEND", "codex", raising=False)
    monkeypatch.setattr(claude_runner, "CODEX_CLI", "codex", raising=False)

    tool_calls = []

    async def collect_tool(name, inp):
        tool_calls.append((name, inp))

    text, session_id, used_fallback = asyncio.run(
        run_claude("hi", model="gpt-5.5", cwd="/workspace/guyu", on_tool_use=collect_tool)
    )

    assert text == "Codex answer"
    assert session_id == "thread_123"
    assert used_fallback is False
    assert captured["args"][:3] == ("codex", "exec", "--json")
    assert "--dangerously-bypass-approvals-and-sandbox" in captured["args"]
    assert "--skip-git-repo-check" in captured["args"]
    assert captured["kwargs"]["cwd"] == "/workspace/guyu"
    assert proc.stdin.buffer.endswith(b"hi\n")
    assert tool_calls == [("command_execution", {"command": "/bin/bash -lc pwd"})]


def test_run_claude_codex_backend_resumes_thread(monkeypatch):
    proc = FakeProc([
        b'{"type":"thread.started","thread_id":"thread_old"}\n',
        b'{"type":"item.completed","item":{"type":"agent_message","text":"Resumed"}}\n',
    ])
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(claude_runner, "AGENT_BACKEND", "codex", raising=False)
    monkeypatch.setattr(claude_runner, "CODEX_CLI", "codex", raising=False)

    text, session_id, used_fallback = asyncio.run(run_claude("again", session_id="thread_old"))

    assert text == "Resumed"
    assert session_id == "thread_old"
    assert used_fallback is False
    assert captured["args"][:4] == ("codex", "exec", "resume", "--json")
    assert "thread_old" in captured["args"]


def test_run_claude_codex_backend_passes_reasoning_effort(monkeypatch):
    proc = FakeProc([
        b'{"type":"thread.started","thread_id":"thread_effort"}\n',
        b'{"type":"item.completed","item":{"type":"agent_message","text":"Effort"}}\n',
    ])
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(claude_runner, "AGENT_BACKEND", "codex", raising=False)
    monkeypatch.setattr(claude_runner, "CODEX_CLI", "codex", raising=False)

    text, session_id, used_fallback = asyncio.run(run_claude("hi", effort="high"))

    assert text == "Effort"
    assert session_id == "thread_effort"
    assert used_fallback is False
    assert "-c" in captured["args"]
    assert 'model_reasoning_effort="high"' in captured["args"]


def test_run_claude_codex_backend_passes_native_fast_service_tier(monkeypatch):
    proc = FakeProc([
        b'{"type":"thread.started","thread_id":"thread_fast"}\n',
        b'{"type":"item.completed","item":{"type":"agent_message","text":"Fast"}}\n',
    ])
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(claude_runner, "AGENT_BACKEND", "codex", raising=False)
    monkeypatch.setattr(claude_runner, "CODEX_CLI", "codex", raising=False)

    text, session_id, used_fallback = asyncio.run(run_claude("hi", service_tier="fast"))

    assert text == "Fast"
    assert session_id == "thread_fast"
    assert used_fallback is False
    assert 'service_tier="fast"' in captured["args"]
    assert "features.fast_mode=true" in captured["args"]
