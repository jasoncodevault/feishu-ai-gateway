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
