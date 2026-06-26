"""
飞书 API 异步封装。
流式方案：发送内联卡片消息 → 用 patch 逐步更新内容（比 cardkit 流式卡片更简单可靠）。
"""

import asyncio
import json
import os
import re
import tempfile
import time
from typing import Optional

import lark_oapi as lark
from lark_oapi.api.im.v1.model import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    DeleteMessageRequest,
    GetMessageRequest,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)


_LOCAL_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _is_remote_or_feishu_image_ref(ref: str) -> bool:
    ref = ref.strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", ref):
        return True
    # Feishu image_key values are opaque IDs, not absolute/local filesystem paths.
    return bool(re.match(r"^(img|image|avatar|\w+)[A-Za-z0-9_\-:.]{8,}$", ref)) and not ref.startswith(("/", "./", "../", "~"))


def _sanitize_card_markdown(content: str) -> str:
    """Prevent Feishu cards from treating local paths as image_key values."""
    def repl(match: re.Match) -> str:
        alt = (match.group(1) or "图片").strip() or "图片"
        ref = match.group(2).strip()
        if _is_remote_or_feishu_image_ref(ref):
            return match.group(0)
        return f"📎 {alt}：`{ref}`（已作为附件发送或保留为本地路径）"

    return _LOCAL_MARKDOWN_IMAGE_RE.sub(repl, content or "")


def _card_json(content: str, loading: bool = False) -> str:
    """
    生成卡片 JSON 字符串（Card JSON 2.0）

    飞书卡片 markdown 元素有长度限制（约 3000 字符），
    超过限制时自动分段为多个 markdown 元素。
    """
    content = _sanitize_card_markdown(content or "")
    elements = []
    if loading:
        elements.append({"tag": "markdown", "content": "⏳ 思考中..."})
    else:
        # 飞书 markdown 元素长度限制约 3000 字符，保守使用 2800
        MAX_CHUNK_SIZE = 2800

        if len(content) <= MAX_CHUNK_SIZE:
            # 内容不长，直接发送
            elements.append({"tag": "markdown", "content": content})
        else:
            # 内容过长，分段发送
            # 尝试按段落分割，避免在句子中间截断
            chunks = []
            current_chunk = ""

            # 按换行符分割
            lines = content.split('\n')

            for line in lines:
                # 如果单行就超过限制，强制截断
                if len(line) > MAX_CHUNK_SIZE:
                    # 先保存当前块
                    if current_chunk:
                        chunks.append(current_chunk)
                        current_chunk = ""

                    # 强制分割长行
                    for i in range(0, len(line), MAX_CHUNK_SIZE):
                        chunks.append(line[i:i + MAX_CHUNK_SIZE])
                    continue

                # 检查加上这行是否会超过限制
                if len(current_chunk) + len(line) + 1 > MAX_CHUNK_SIZE:
                    # 超过限制，保存当前块，开始新块
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = line
                else:
                    # 未超过限制，追加到当前块
                    if current_chunk:
                        current_chunk += '\n' + line
                    else:
                        current_chunk = line

            # 保存最后一块
            if current_chunk:
                chunks.append(current_chunk)

            # 为每个块创建 markdown 元素
            for i, chunk in enumerate(chunks):
                # 第一块不加前缀，后续块加分段标记
                if i > 0:
                    chunk = f"**（续 {i}）**\n\n{chunk}"
                elements.append({"tag": "markdown", "content": chunk})

    return json.dumps({
        "schema": "2.0",
        "body": {"elements": elements},
    }, ensure_ascii=False)


class FeishuClient:
    def __init__(self, client: lark.Client, app_id: str = "", app_secret: str = ""):
        self.client = client
        self._app_id = app_id
        self._app_secret = app_secret

    async def _retry_with_backoff(self, coro_func, max_retries: int = 3, initial_delay: float = 0.5):
        """
        执行异步操作，失败时指数退避重试。

        Args:
            coro_func: 返回 coroutine 的可调用对象
            max_retries: 最多重试次数（不包括首次尝试）
            initial_delay: 初始延迟秒数

        Returns:
            操作结果

        Raises:
            最后一次尝试的异常
        """
        delay = initial_delay
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                return await coro_func()
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    print(f"[retry] 第 {attempt + 1} 次失败，{delay:.1f}s 后重试: {e}", flush=True)
                    await asyncio.sleep(delay)
                    delay *= 2  # 指数退避
                else:
                    print(f"[retry] 已达最大重试次数 {max_retries + 1}，放弃", flush=True)

        raise last_error

    # ── 发送消息 ──────────────────────────────────────────────

    async def send_card_to_user(self, open_id: str, content: str = "", loading: bool = True) -> str:
        """向用户发送卡片消息，返回 message_id（带重试）"""
        async def _send():
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(open_id)
                    .msg_type("interactive")
                    .content(_card_json(content, loading=loading))
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.acreate(req)
            if not resp.success():
                raise RuntimeError(f"发送卡片消息失败: {resp.code} {resp.msg}")
            return resp.data.message_id

        return await self._retry_with_backoff(_send, max_retries=3)

    async def reply_card(
        self,
        message_id: str,
        content: str = "",
        loading: bool = True,
        reply_in_thread: bool = False,
    ) -> str:
        """回复用户消息（卡片形式），触发通知。返回回复消息的 message_id（带重试）"""
        async def _reply():
            req = (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type("interactive")
                    .content(_card_json(content, loading=loading))
                    .reply_in_thread(reply_in_thread)
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.areply(req)
            if not resp.success():
                raise RuntimeError(f"回复卡片消息失败: {resp.code} {resp.msg}")
            return resp.data.message_id

        return await self._retry_with_backoff(_reply, max_retries=3)

    async def update_card(self, message_id: str, content: str):
        """用 patch 更新已发送的卡片内容（带重试）"""
        async def _update():
            req = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(_card_json(content, loading=False))
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.apatch(req)
            if not resp.success():
                raise RuntimeError(f"patch 卡片失败: {resp.code} {resp.msg}")

        await self._retry_with_backoff(_update, max_retries=3)

    # ── 媒体上传/发送 ─────────────────────────────────────────

    async def upload_image(self, path: str) -> str:
        """Upload a local image file and return Feishu image_key."""
        from lark_oapi.api.im.v1.model import CreateImageRequest, CreateImageRequestBody

        async def _upload():
            with open(path, "rb") as f:
                req = (
                    CreateImageRequest.builder()
                    .request_body(
                        CreateImageRequestBody.builder()
                        .image_type("message")
                        .image(f)
                        .build()
                    )
                    .build()
                )
                resp = await self.client.im.v1.image.acreate(req)
            if not resp.success():
                raise RuntimeError(f"上传图片失败: {resp.code} {resp.msg}")
            return resp.data.image_key

        return await self._retry_with_backoff(_upload, max_retries=2)

    @staticmethod
    def _file_type_for_path(path: str) -> str:
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if ext in ("ppt", "pptx"):
            return "ppt"
        if ext in ("doc", "docx"):
            return "doc"
        if ext in ("xls", "xlsx", "csv"):
            return "xls"
        if ext == "pdf":
            return "pdf"
        if ext in ("mp4", "mov", "m4v"):
            return "mp4"
        if ext in ("opus", "ogg"):
            return "opus"
        return "stream"

    async def upload_file(self, path: str, file_name: str | None = None) -> str:
        """Upload a local file and return Feishu file_key."""
        from lark_oapi.api.im.v1.model import CreateFileRequest, CreateFileRequestBody

        file_name = file_name or os.path.basename(path)
        file_type = self._file_type_for_path(file_name or path)

        async def _upload():
            with open(path, "rb") as f:
                req = (
                    CreateFileRequest.builder()
                    .request_body(
                        CreateFileRequestBody.builder()
                        .file_type(file_type)
                        .file_name(file_name)
                        .file(f)
                        .build()
                    )
                    .build()
                )
                resp = await self.client.im.v1.file.acreate(req)
            if not resp.success():
                raise RuntimeError(f"上传文件失败: {resp.code} {resp.msg}")
            return resp.data.file_key

        return await self._retry_with_backoff(_upload, max_retries=2)

    async def send_image_to_user(self, open_id: str, path: str) -> str:
        image_key = await self.upload_image(path)
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("image")
                .content(json.dumps({"image_key": image_key}))
                .build()
            )
            .build()
        )
        resp = await self.client.im.v1.message.acreate(req)
        if not resp.success():
            raise RuntimeError(f"发送图片失败: {resp.code} {resp.msg}")
        return resp.data.message_id

    async def reply_image(self, message_id: str, path: str, reply_in_thread: bool = False) -> str:
        image_key = await self.upload_image(path)
        req = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("image")
                .content(json.dumps({"image_key": image_key}))
                .reply_in_thread(reply_in_thread)
                .build()
            )
            .build()
        )
        resp = await self.client.im.v1.message.areply(req)
        if not resp.success():
            raise RuntimeError(f"回复图片失败: {resp.code} {resp.msg}")
        return resp.data.message_id

    async def send_file_to_user(self, open_id: str, path: str, file_name: str | None = None) -> str:
        file_key = await self.upload_file(path, file_name=file_name)
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("file")
                .content(json.dumps({"file_key": file_key}))
                .build()
            )
            .build()
        )
        resp = await self.client.im.v1.message.acreate(req)
        if not resp.success():
            raise RuntimeError(f"发送文件失败: {resp.code} {resp.msg}")
        return resp.data.message_id

    async def reply_file(
        self,
        message_id: str,
        path: str,
        file_name: str | None = None,
        reply_in_thread: bool = False,
    ) -> str:
        file_key = await self.upload_file(path, file_name=file_name)
        req = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("file")
                .content(json.dumps({"file_key": file_key}))
                .reply_in_thread(reply_in_thread)
                .build()
            )
            .build()
        )
        resp = await self.client.im.v1.message.areply(req)
        if not resp.success():
            raise RuntimeError(f"回复文件失败: {resp.code} {resp.msg}")
        return resp.data.message_id

    async def download_image(self, message_id: str, image_key: str) -> str:
        """下载飞书图片到临时文件，返回本地路径（不阻塞事件循环）"""
        return await asyncio.to_thread(
            self._download_image_sync, message_id, image_key
        )

    def _download_image_sync(self, message_id: str, image_key: str) -> str:
        """同步下载逻辑，在线程池中执行"""
        import ssl
        import urllib.request
        import uuid

        ctx = ssl.create_default_context()

        token_body = json.dumps({"app_id": self._app_id, "app_secret": self._app_secret}).encode()
        token_req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=token_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(token_req, context=ctx, timeout=10) as r:
            token = json.loads(r.read())["tenant_access_token"]

        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{image_key}?type=image"
        img_req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        tmp_path = os.path.join(tempfile.gettempdir(), f"feishu-img-{uuid.uuid4().hex[:8]}.jpg")
        with urllib.request.urlopen(img_req, context=ctx, timeout=15) as r:
            ct = r.headers.get("Content-Type", "")
            if "png" in ct:
                tmp_path = tmp_path.replace(".jpg", ".png")
            elif "gif" in ct:
                tmp_path = tmp_path.replace(".jpg", ".gif")
            with open(tmp_path, "wb") as f:
                f.write(r.read())

        return tmp_path

    async def download_file(self, message_id: str, file_key: str, file_name: str | None = None) -> str:
        """下载飞书文件到临时文件，返回本地路径（不阻塞事件循环）"""
        return await asyncio.to_thread(self._download_file_sync, message_id, file_key, file_name)

    def _download_file_sync(self, message_id: str, file_key: str, file_name: str | None = None) -> str:
        """同步下载文件逻辑，在线程池中执行。"""
        import ssl
        import urllib.request
        import uuid

        ctx = ssl.create_default_context()
        token_body = json.dumps({"app_id": self._app_id, "app_secret": self._app_secret}).encode()
        token_req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=token_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(token_req, context=ctx, timeout=10) as r:
            token = json.loads(r.read())["tenant_access_token"]

        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}?type=file"
        file_req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        safe_name = os.path.basename(file_name or f"feishu-file-{uuid.uuid4().hex[:8]}")
        if not safe_name:
            safe_name = f"feishu-file-{uuid.uuid4().hex[:8]}"
        tmp_path = os.path.join(tempfile.gettempdir(), safe_name)
        if os.path.exists(tmp_path):
            stem, ext = os.path.splitext(safe_name)
            tmp_path = os.path.join(tempfile.gettempdir(), f"{stem}-{uuid.uuid4().hex[:6]}{ext}")
        with urllib.request.urlopen(file_req, context=ctx, timeout=60) as r:
            with open(tmp_path, "wb") as f:
                f.write(r.read())
        return tmp_path

    async def update_card_with_buttons(self, message_id: str, content: str, buttons: list[dict],
                                      flow: bool = False):
        """更新卡片内容并附加操作按钮。flow=True 时横排自动换行，False 时竖排。"""
        base = json.loads(_card_json(content))
        btn_elements = []
        for i, btn in enumerate(buttons):
            btn_elements.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": btn["text"]},
                "type": "default",
                "size": "small",
                "name": f"btn_{i}",
                "value": btn["value"],
                "behaviors": [{"type": "callback", "value": btn["value"]}],
            })
        if flow and btn_elements:
            # 横排: column_set + flex_mode flow
            columns = [{"tag": "column", "width": "auto", "elements": [b]} for b in btn_elements]
            base["body"]["elements"].append({"tag": "column_set", "flex_mode": "flow", "columns": columns})
        else:
            # 竖排: 每个按钮独占一行
            base["body"]["elements"].extend(btn_elements)
        card_content = json.dumps(base, ensure_ascii=False)

        async def _update():
            req = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(card_content)
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.apatch(req)
            if not resp.success():
                raise RuntimeError(f"patch 卡片失败: {resp.code} {resp.msg}")

        await self._retry_with_backoff(_update, max_retries=3)

    async def update_card_elements(self, message_id: str, elements: list[dict]):
        """用自定义 elements 列表更新卡片（支持 markdown + button 混排）"""
        card_content = json.dumps({
            "schema": "2.0",
            "body": {"elements": elements},
        }, ensure_ascii=False)

        async def _update():
            req = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(card_content)
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.apatch(req)
            if not resp.success():
                raise RuntimeError(f"patch 卡片失败: {resp.code} {resp.msg}")

        await self._retry_with_backoff(_update, max_retries=3)

    async def reply_text(self, message_id: str, text: str, reply_in_thread: bool = False) -> str:
        """回复纯文本消息（触发通知）"""
        async def _reply():
            req = (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type("text")
                    .content(json.dumps({"text": text}))
                    .reply_in_thread(reply_in_thread)
                    .build()
                )
                .build()
            )
            resp = await self.client.im.v1.message.areply(req)
            if not resp.success():
                raise RuntimeError(f"回复文本消息失败: {resp.code} {resp.msg}")
            return resp.data.message_id

        return await self._retry_with_backoff(_reply, max_retries=2)

    async def recall_message(self, message_id: str):
        """撤回机器人自己发送的消息。"""
        req = DeleteMessageRequest.builder().message_id(message_id).build()
        resp = await self.client.im.v1.message.adelete(req)
        if not resp.success():
            raise RuntimeError(f"撤回消息失败: {resp.code} {resp.msg}")

    async def get_message_items(self, message_id: str) -> list:
        """Return message items for a message_id.

        For ``merge_forward`` messages, Feishu's get-message API returns the
        wrapper message plus its child messages in ``data.items``. Normal
        messages return a single item.
        """
        async def _get():
            req = GetMessageRequest.builder().message_id(message_id).build()
            resp = await self.client.im.v1.message.aget(req)
            if not resp.success():
                raise RuntimeError(f"获取消息内容失败: {resp.code} {resp.msg}")
            data = resp.data
            return list(getattr(data, "items", None) or [])

        return await self._retry_with_backoff(_get, max_retries=2)

    async def send_text_to_user(self, open_id: str, text: str) -> str:
        """发送纯文本消息"""
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        resp = await self.client.im.v1.message.acreate(req)
        if not resp.success():
            raise RuntimeError(f"发送文本消息失败: {resp.code} {resp.msg}")
        return resp.data.message_id
