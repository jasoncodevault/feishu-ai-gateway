"""
Integration tests for group chat functionality.
Tests message handling, session isolation, and chat detection.
"""
import os
import pytest
from unittest.mock import AsyncMock, MagicMock

# Set environment variables before importing modules
os.environ.setdefault("FEISHU_APP_ID", "test_app_id")
os.environ.setdefault("FEISHU_APP_SECRET", "test_app_secret")
os.environ.setdefault("FEISHU_VERIFICATION_TOKEN", "test_token")
os.environ.setdefault("FEISHU_ENCRYPT_KEY", "test_key")

from main import extract_chat_info, _chat_key_has_topic, _message_reply_target_id
from session_store import SessionStore


@pytest.fixture
def session_store():
    """Create a session store for testing"""
    return SessionStore()


# ── Test extract_chat_info ──────────────────────────────────

def test_extract_chat_info_private_chat():
    """Test extracting chat info from private chat message"""
    mock_event = MagicMock()
    mock_event.event.sender.sender_id.open_id = "user_123"
    mock_event.event.message.chat_type = "p2p"
    mock_event.event.message.chat_id = "user_123"
    mock_event.event.message.thread_id = None
    mock_event.event.message.root_id = None

    user_id, chat_id, is_group = extract_chat_info(mock_event)

    assert user_id == "user_123"
    assert chat_id == "user_123"
    assert is_group is False


def test_extract_chat_info_private_reply_ignores_thread_ids():
    """P2P replies carry thread/root IDs but must stay on the private session key."""
    mock_event = MagicMock()
    mock_event.event.sender.sender_id.open_id = "user_123"
    mock_event.event.message.chat_type = "p2p"
    mock_event.event.message.chat_id = "oc_p2p_chat"
    mock_event.event.message.thread_id = "omt_topic_123"
    mock_event.event.message.root_id = "om_root_ignored"

    user_id, chat_id, is_group = extract_chat_info(mock_event)

    assert user_id == "user_123"
    assert chat_id == "user_123"
    assert is_group is False
    assert _chat_key_has_topic(chat_id) is False


def test_extract_chat_info_group_chat():
    """Test extracting chat info from group chat message"""
    mock_event = MagicMock()
    mock_event.event.sender.sender_id.open_id = "user_456"
    mock_event.event.message.chat_type = "group"
    mock_event.event.message.chat_id = "group_789"
    mock_event.event.message.thread_id = None
    mock_event.event.message.root_id = None

    user_id, chat_id, is_group = extract_chat_info(mock_event)

    assert user_id == "user_456"
    assert chat_id == "group_789"
    assert is_group is True
    assert _chat_key_has_topic(chat_id) is False


def test_extract_chat_info_topic_group_uses_thread_id():
    """Topic group messages use thread_id as part of the session key."""
    mock_event = MagicMock()
    mock_event.event.sender.sender_id.open_id = "user_456"
    mock_event.event.message.chat_type = "group"
    mock_event.event.message.chat_id = "group_789"
    mock_event.event.message.thread_id = "omt_topic_123"
    mock_event.event.message.root_id = "om_root_ignored"

    user_id, chat_id, is_group = extract_chat_info(mock_event)

    assert user_id == "user_456"
    assert chat_id == "group_789#topic:omt_topic_123"
    assert is_group is True
    assert _chat_key_has_topic(chat_id) is True


def test_extract_chat_info_topic_group_falls_back_to_root_id():
    """Topic replies without thread_id still isolate on root_id."""
    mock_event = MagicMock()
    mock_event.event.sender.sender_id.open_id = "user_456"
    mock_event.event.message.chat_type = "group"
    mock_event.event.message.chat_id = "group_789"
    mock_event.event.message.thread_id = None
    mock_event.event.message.root_id = "om_root_123"

    user_id, chat_id, is_group = extract_chat_info(mock_event)

    assert user_id == "user_456"
    assert chat_id == "group_789#topic:om_root_123"
    assert is_group is True
    assert _chat_key_has_topic(chat_id) is True


def test_topic_reply_target_prefers_parent_then_root_before_child_message():
    """Replies inside topics should target the root/parent, not child reply."""
    msg = MagicMock()
    msg.message_id = "om_child"
    msg.parent_id = "om_parent"
    msg.root_id = "om_root"

    assert _message_reply_target_id(msg) == "om_parent"


def test_topic_reply_target_falls_back_to_root_id():
    msg = MagicMock()
    msg.message_id = "om_child"
    msg.parent_id = None
    msg.root_id = "om_root"

    assert _message_reply_target_id(msg) == "om_root"


def test_plain_reply_target_uses_message_id():
    msg = MagicMock()
    msg.message_id = "om_plain"
    msg.parent_id = None
    msg.root_id = None

    assert _message_reply_target_id(msg) == "om_plain"


# ── Test session isolation ──────────────────────────────────

@pytest.mark.asyncio
async def test_session_isolation_private_vs_group(session_store):
    """Test that private and group chats have isolated sessions"""
    user_id = "user_123"
    private_chat_id = user_id
    group_chat_id = "group_456"

    await session_store.set_model(user_id, private_chat_id, "claude-opus")
    await session_store.set_model(user_id, group_chat_id, "claude-sonnet")

    private_check = await session_store.get_current(user_id, private_chat_id)
    group_check = await session_store.get_current(user_id, group_chat_id)

    assert private_check.model == "claude-opus"
    assert group_check.model == "claude-sonnet"


@pytest.mark.asyncio
async def test_multiple_groups_isolation(session_store):
    """Test that multiple groups have independent sessions"""
    user_id = "user_123"
    group1_id = "group_001"
    group2_id = "group_002"

    await session_store.set_model(user_id, group1_id, "claude-opus")
    await session_store.set_model(user_id, group2_id, "claude-sonnet")

    group1_session = await session_store.get_current(user_id, group1_id)
    group2_session = await session_store.get_current(user_id, group2_id)

    assert group1_session.model == "claude-opus"
    assert group2_session.model == "claude-sonnet"


@pytest.mark.asyncio
async def test_multiple_users_in_same_group(session_store):
    """Test that different users in same group have separate sessions"""
    group_id = "group_123"
    user1_id = "user_001"
    user2_id = "user_002"

    await session_store.set_model(user1_id, group_id, "claude-opus")
    await session_store.set_model(user2_id, group_id, "claude-sonnet")

    user1_session = await session_store.get_current(user1_id, group_id)
    user2_session = await session_store.get_current(user2_id, group_id)

    assert user1_session.model == "claude-opus"
    assert user2_session.model == "claude-sonnet"


# ── Test session operations with chat_id ──────────────────

@pytest.mark.asyncio
async def test_set_permission_mode_with_chat_id(session_store):
    """Test setting permission mode for specific chat"""
    user_id = "user_123"
    chat_id = "group_456"

    await session_store.set_permission_mode(user_id, chat_id, "restricted")
    session = await session_store.get_current(user_id, chat_id)

    assert session.permission_mode == "restricted"


@pytest.mark.asyncio
async def test_set_cwd_with_chat_id(session_store):
    """Test setting working directory for specific chat"""
    user_id = "user_123"
    chat_id = "group_456"

    await session_store.set_cwd(user_id, chat_id, "/tmp/work")
    session = await session_store.get_current(user_id, chat_id)

    assert session.cwd == "/tmp/work"


@pytest.mark.asyncio
async def test_new_session_with_chat_id(session_store):
    """Test creating new session for specific chat"""
    user_id = "user_123"
    chat_id = "group_456"

    await session_store.new_session(user_id, chat_id)
    session = await session_store.get_current(user_id, chat_id)

    assert session.session_id is None


@pytest.mark.asyncio
async def test_list_sessions_with_chat_id(session_store):
    """Test listing sessions for specific chat"""
    user_id = "user_list_test"
    chat_id = "group_list_test"

    sessions = await session_store.list_sessions(user_id, chat_id)
    assert isinstance(sessions, list)


@pytest.mark.asyncio
async def test_resume_session_with_chat_id(session_store):
    """Test resuming session in specific chat"""
    user_id = "user_resume_test"
    chat_id = "group_resume_test"

    resumed_id, old_title = await session_store.resume_session(user_id, chat_id, "1")
    assert resumed_id is None
    assert old_title == ""
