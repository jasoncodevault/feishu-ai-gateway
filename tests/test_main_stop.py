import asyncio
import os
import sys
import types
import unittest
from unittest import mock

os.environ.setdefault("FEISHU_APP_ID", "test-app-id")
os.environ.setdefault("FEISHU_APP_SECRET", "test-app-secret")


def _install_fake_lark():
    if "lark_oapi" in sys.modules:
        return

    class _Builder:
        def app_id(self, *_args, **_kwargs):
            return self

        def app_secret(self, *_args, **_kwargs):
            return self

        def log_level(self, *_args, **_kwargs):
            return self

        def request_body(self, *_args, **_kwargs):
            return self

        def receive_id_type(self, *_args, **_kwargs):
            return self

        def receive_id(self, *_args, **_kwargs):
            return self

        def msg_type(self, *_args, **_kwargs):
            return self

        def content(self, *_args, **_kwargs):
            return self

        def message_id(self, *_args, **_kwargs):
            return self

        def event_handler(self, *_args, **_kwargs):
            return self

        def register_p2_im_message_receive_v1(self, *_args, **_kwargs):
            return self

        def build(self):
            return self

    class _Client:
        @staticmethod
        def builder():
            return _Builder()

    class _WsClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            return None

    fake_lark = types.ModuleType("lark_oapi")
    fake_lark.Client = _Client
    fake_lark.LogLevel = types.SimpleNamespace(INFO="INFO")
    fake_lark.ws = types.SimpleNamespace(Client=_WsClient)
    fake_lark.EventDispatcherHandler = types.SimpleNamespace(builder=lambda *_args, **_kwargs: _Builder())

    model_mod = types.ModuleType("lark_oapi.api.im.v1.model")
    for name in (
        "P2ImMessageReceiveV1",
        "CreateMessageRequest",
        "CreateMessageRequestBody",
        "DeleteMessageRequest",
        "PatchMessageRequest",
        "PatchMessageRequestBody",
        "ReplyMessageRequest",
        "ReplyMessageRequestBody",
    ):
        setattr(model_mod, name, type(name, (), {"builder": staticmethod(lambda: _Builder())}))

    sys.modules["lark_oapi"] = fake_lark
    sys.modules["lark_oapi.api"] = types.ModuleType("lark_oapi.api")
    sys.modules["lark_oapi.api.im"] = types.ModuleType("lark_oapi.api.im")
    sys.modules["lark_oapi.api.im.v1"] = types.ModuleType("lark_oapi.api.im.v1")
    sys.modules["lark_oapi.api.im.v1.model"] = model_mod

    callback_model_mod = types.ModuleType("lark_oapi.event.callback.model.p2_card_action_trigger")
    for name in ("P2CardActionTrigger", "P2CardActionTriggerResponse", "CallBackToast"):
        setattr(callback_model_mod, name, type(name, (), {"builder": staticmethod(lambda: _Builder())}))
    sys.modules["lark_oapi.event"] = types.ModuleType("lark_oapi.event")
    sys.modules["lark_oapi.event.callback"] = types.ModuleType("lark_oapi.event.callback")
    sys.modules["lark_oapi.event.callback.model"] = types.ModuleType("lark_oapi.event.callback.model")
    sys.modules["lark_oapi.event.callback.model.p2_card_action_trigger"] = callback_model_mod


_install_fake_lark()

import main


class MainStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_stop_command_returns_no_active_run_message(self):
        with mock.patch.object(main, "stop_run", mock.AsyncMock(return_value=False)):
            reply = await main._handle_stop_command("user-1")

        self.assertIn("没有正在运行", reply)

    async def test_handle_stop_command_requests_stop_for_active_run(self):
        active_run = mock.Mock(stop_requested=False)

        with mock.patch.object(
            main._active_runs,
            "get_run",
            return_value=active_run,
        ), mock.patch.object(
            main,
            "stop_run",
            mock.AsyncMock(return_value=True),
        ) as stop_run_mock:
            reply = await main._handle_stop_command("user-1")

        stop_run_mock.assert_awaited_once()
        self.assertIn("已发送停止请求", reply)

    async def test_completion_notice_private_is_short_done(self):
        with mock.patch.object(main.feishu, "send_text_to_user", mock.AsyncMock()) as send_mock:
            await main._send_completion_notice("user-1", is_group=False, notify_msg_id="msg-1")

        send_mock.assert_awaited_once_with("user-1", "✅ 已完成")

    async def test_completion_notice_group_replies_to_original_message(self):
        with mock.patch.object(main.feishu, "reply_text", mock.AsyncMock()) as reply_mock:
            await main._send_completion_notice("user-1", is_group=True, notify_msg_id="msg-1")

        reply_mock.assert_awaited_once_with("msg-1", "✅ 已完成", reply_in_thread=False)

    async def test_completion_notice_topic_reply_sets_thread_flag(self):
        with mock.patch.object(main.feishu, "reply_text", mock.AsyncMock()) as reply_mock:
            await main._send_completion_notice(
                "user-1",
                is_group=True,
                notify_msg_id="msg-1",
                reply_in_thread=True,
            )

        reply_mock.assert_awaited_once_with("msg-1", "✅ 已完成", reply_in_thread=True)

    async def test_completion_notice_private_topic_uses_reply_api(self):
        with mock.patch.object(main.feishu, "reply_text", mock.AsyncMock()) as reply_mock, \
             mock.patch.object(main.feishu, "send_text_to_user", mock.AsyncMock()) as send_mock:
            await main._send_completion_notice(
                "user-1",
                is_group=False,
                notify_msg_id="msg-1",
                reply_in_thread=True,
            )

        reply_mock.assert_awaited_once_with("msg-1", "✅ 已完成", reply_in_thread=True)
        send_mock.assert_not_awaited()

    async def test_should_final_repost_only_when_duration_exceeds_30s_without_options(self):
        self.assertFalse(main._should_final_repost(30.0, has_options=False))
        self.assertTrue(main._should_final_repost(30.1, has_options=False))
        self.assertFalse(main._should_final_repost(31.0, has_options=True))

    async def test_repost_final_and_recall_private_recalls_stream_card_then_sends_final_card(self):
        calls = []
        async def send_side_effect(*_args, **_kwargs):
            calls.append("send")
        async def recall_side_effect(*_args, **_kwargs):
            calls.append("recall")
        with mock.patch.object(main.feishu, "send_card_to_user", mock.AsyncMock(side_effect=send_side_effect)) as send_mock, \
             mock.patch.object(main.feishu, "recall_message", mock.AsyncMock(side_effect=recall_side_effect)) as recall_mock:
            await main._repost_final_and_recall(
                "user-1",
                is_group=False,
                notify_msg_id="origin-msg",
                card_msg_id="stream-msg",
                final="最终答案",
            )

        recall_mock.assert_awaited_once_with("stream-msg")
        send_mock.assert_awaited_once_with("user-1", content="最终答案", loading=False)
        self.assertEqual(calls, ["recall", "send"])

    async def test_repost_final_and_recall_group_recalls_stream_card_then_replies_final_card(self):
        calls = []
        async def reply_side_effect(*_args, **_kwargs):
            calls.append("reply")
        async def recall_side_effect(*_args, **_kwargs):
            calls.append("recall")
        with mock.patch.object(main.feishu, "reply_card", mock.AsyncMock(side_effect=reply_side_effect)) as reply_mock, \
             mock.patch.object(main.feishu, "recall_message", mock.AsyncMock(side_effect=recall_side_effect)) as recall_mock:
            await main._repost_final_and_recall(
                "user-1",
                is_group=True,
                notify_msg_id="origin-msg",
                card_msg_id="stream-msg",
                final="最终答案",
            )

        recall_mock.assert_awaited_once_with("stream-msg")
        reply_mock.assert_awaited_once_with(
            "origin-msg",
            content="最终答案",
            loading=False,
            reply_in_thread=False,
        )
        self.assertEqual(calls, ["recall", "reply"])

    async def test_repost_final_and_recall_topic_reply_sets_thread_flag(self):
        with mock.patch.object(main.feishu, "reply_card", mock.AsyncMock()) as reply_mock, \
             mock.patch.object(main.feishu, "recall_message", mock.AsyncMock()):
            await main._repost_final_and_recall(
                "user-1",
                is_group=True,
                notify_msg_id="origin-msg",
                card_msg_id="stream-msg",
                final="最终答案",
                reply_in_thread=True,
            )

        reply_mock.assert_awaited_once_with(
            "origin-msg",
            content="最终答案",
            loading=False,
            reply_in_thread=True,
        )

    async def test_repost_final_and_recall_private_topic_uses_reply_api(self):
        with mock.patch.object(main.feishu, "reply_card", mock.AsyncMock()) as reply_mock, \
             mock.patch.object(main.feishu, "send_card_to_user", mock.AsyncMock()) as send_mock, \
             mock.patch.object(main.feishu, "recall_message", mock.AsyncMock()):
            await main._repost_final_and_recall(
                "user-1",
                is_group=False,
                notify_msg_id="origin-msg",
                card_msg_id="stream-msg",
                final="最终答案",
                reply_in_thread=True,
            )

        reply_mock.assert_awaited_once_with(
            "origin-msg",
            content="最终答案",
            loading=False,
            reply_in_thread=True,
        )
        send_mock.assert_not_awaited()

    async def test_repost_final_and_recall_skips_final_card_when_recall_fails(self):
        with mock.patch.object(main.feishu, "send_card_to_user", mock.AsyncMock()) as send_mock, \
             mock.patch.object(main.feishu, "recall_message", mock.AsyncMock(side_effect=RuntimeError("no permission"))) as recall_mock:
            result = await main._repost_final_and_recall(
                "user-1",
                is_group=False,
                notify_msg_id="origin-msg",
                card_msg_id="stream-msg",
                final="最终答案",
            )

        self.assertTrue(result)
        recall_mock.assert_awaited_once_with("stream-msg")
        send_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
