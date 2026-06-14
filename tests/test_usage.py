import json
import os
import ssl
import time
import urllib.request
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
