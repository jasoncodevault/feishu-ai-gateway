import os
import shutil
from dotenv import load_dotenv

load_dotenv()

FEISHU_APP_ID = os.environ["FEISHU_APP_ID"]
FEISHU_APP_SECRET = os.environ["FEISHU_APP_SECRET"]

AGENT_BACKEND = os.getenv("AGENT_BACKEND", "claude").strip().lower()
CLAUDE_CLI = os.getenv("CLAUDE_CLI_PATH") or shutil.which("claude") or "claude"
CODEX_CLI = os.getenv("CODEX_CLI_PATH") or shutil.which("codex") or "codex"

DEFAULT_MODEL = os.getenv(
    "DEFAULT_MODEL",
    "gpt-5.5" if AGENT_BACKEND == "codex" else "claude-opus-4-6",
)
DEFAULT_CWD = os.path.expanduser(os.getenv("DEFAULT_CWD", "~"))
PERMISSION_MODE = os.getenv("PERMISSION_MODE", "bypassPermissions")
DEFAULT_EFFORT = os.getenv("DEFAULT_EFFORT", os.getenv("CLAUDE_CODE_EFFORT_LEVEL", "auto"))

SESSIONS_DIR = os.path.expanduser("~/.feishu-claude")

# 卡片按钮回调 HTTP 端口；CALLBACK_PUBLIC_URL 可显式指定 Cloudflare/ngrok 暴露地址
CALLBACK_PORT = int(os.getenv("CALLBACK_PORT", "9981"))
CALLBACK_PUBLIC_URL = os.getenv("CALLBACK_PUBLIC_URL", "").rstrip("/")

# 流式卡片更新：每积累多少字符推送一次
STREAM_CHUNK_SIZE = int(os.getenv("STREAM_CHUNK_SIZE", "20"))

# Claude Code Agent tool launches asynchronous/background subagents under --print.
# The Feishu bridge is a one-shot request/response runner, so those background
# agents cannot deliver their completion notification back to Feishu after the
# parent CLI exits. Disable Agent by default; the model should use direct
# WebSearch/WebFetch/Bash work in this bridge instead of promising a later reply.
_default_disallowed = "Agent" if AGENT_BACKEND == "claude" else ""
DISALLOWED_TOOLS = [
    item.strip()
    for item in os.getenv("DISALLOWED_TOOLS", _default_disallowed).replace(";", ",").split(",")
    if item.strip()
]

FEISHU_BRIDGE_SYSTEM_PROMPT = os.getenv(
    "FEISHU_BRIDGE_SYSTEM_PROMPT",
    (
        "你正在飞书 Claude Code 网关中以非交互式 --print 一次性运行。"
        "不要使用 Agent/后台子代理/异步代理；这种后台完成通知无法在父进程退出后回到飞书。"
        "需要研究或并行检索时，直接使用当前会话的工具完成并给出最终答案。"
        "禁止回复‘代理回来后我再给你’、‘稍后补充’等未来承诺；本轮必须给出完整结果，"
        "如果受工具或权限阻塞，则明确报告阻塞和已取得的部分证据。"
    ),
)
