import json
import os
import ssl
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import commands


class _FakeResponse:
    status = 200

    def __init__(self, headers):
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_usage_reads_claude_credentials_on_linux_home(tmp_path, monkeypatch, request):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / ".credentials.json").write_text(
        json.dumps({
            "claudeAiOauth": {
                "accessToken": "test-access-token",
                "subscriptionType": "max",
                "rateLimitTier": "default_claude_max_20x",
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    old_tz = os.environ.get("TZ")

    def restore_tz():
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        if hasattr(time, "tzset"):
            time.tzset()

    request.addfinalizer(restore_tz)
    os.environ["TZ"] = "UTC"
    if hasattr(time, "tzset"):
        time.tzset()
    monkeypatch.setattr(commands.sys, "platform", "linux")

    seen = {}

    def fake_urlopen(req, context=None, timeout=None):
        seen["authorization"] = req.headers.get("Authorization")
        return _FakeResponse({
            "anthropic-ratelimit-unified-5h-utilization": "0.25",
            "anthropic-ratelimit-unified-7d-utilization": "0.5",
            "anthropic-ratelimit-unified-5h-reset": "1781230800",
            "anthropic-ratelimit-unified-7d-reset": "1781535600",
            "anthropic-ratelimit-unified-5h-status": "allowed",
            "anthropic-ratelimit-unified-7d-status": "allowed",
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ssl, "create_default_context", lambda: MagicMock())

    result = commands._get_usage()

    assert seen["authorization"] == "Bearer test-access-token"
    assert "Claude Max 用量" in result
    assert "5小时窗口" in result
    assert "25.0%" in result
    assert "50.0%" in result
    assert "重置时间：06/12 10:20" in result
    assert "目前只支持 macOS" not in result


def test_codex_usage_reads_local_token_count_rollouts(tmp_path, monkeypatch):
    monkeypatch.setattr(commands, "AGENT_BACKEND", "codex", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    sessions = tmp_path / "sessions" / "2026" / "06" / "16"
    sessions.mkdir(parents=True)

    now = datetime.now(timezone.utc)

    def event(ts, total, primary_used=12.0, secondary_used=34.0):
        return {
            "timestamp": ts.isoformat().replace("+00:00", "Z"),
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": total - 10,
                        "cached_input_tokens": 0,
                        "output_tokens": 10,
                        "reasoning_output_tokens": 0,
                        "total_tokens": total,
                    }
                },
                "rate_limits": {
                    "primary": {"used_percent": primary_used, "window_minutes": 300, "resets_at": int(now.timestamp()) + 3600},
                    "secondary": {"used_percent": secondary_used, "window_minutes": 10080, "resets_at": int(now.timestamp()) + 7200},
                    "plan_type": "pro",
                },
            },
        }

    rows = [
        event(now, 1000),
        event(now - timedelta(days=2), 2000),
        event(now - timedelta(days=10), 3000),
    ]
    (sessions / "rollout-test.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    result = commands._get_usage()

    assert "Codex 用量" in result
    assert "今日" in result and "1,000" in result
    assert "近 7 天" in result and "3,000" in result
    assert "累计" in result and "6,000" in result
    assert "5小时窗口" in result
    assert "7天窗口" in result
    assert "Claude Code OAuth" not in result
