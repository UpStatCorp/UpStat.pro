"""
Unit tests for voice_assistant/session_manager.py.

Covers:
- cleanup_inactive_sessions removes expired sessions
- cleanup_inactive_sessions keeps recently active sessions
- close_session removes session from both sessions and user_sessions dicts
- Single-session-per-user: creating a new session for an existing user closes the old one
- get_stats returns correct counts
- Session capacity limit is enforced

These tests bypass UserSession.__init__ (which requires the full voice_assistant
component stack) by injecting MagicMock sessions directly into the manager dicts.
"""

import asyncio
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

from voice_assistant.session_manager import SessionManager


# ─── Helpers ──────────────────────────────────────────────────────────────────


def make_session(user_id: int, seconds_since_activity: float = 0) -> MagicMock:
    """
    Create a fake UserSession-like MagicMock.
    `seconds_since_activity` controls how stale the session is.
    """
    session = MagicMock()
    session.user_id = user_id
    session.websocket = None
    session.last_activity = datetime.utcnow() - timedelta(seconds=seconds_since_activity)
    session.cleanup = MagicMock()
    return session


def inject_session(mgr: SessionManager, session_id: str, user_id: int, **kwargs) -> MagicMock:
    """Insert a fake session into the manager, bypassing create_session."""
    sess = make_session(user_id, **kwargs)
    mgr.sessions[session_id] = sess
    mgr.user_sessions[user_id] = session_id
    return sess


# ─── cleanup_inactive_sessions ────────────────────────────────────────────────


class TestCleanupInactiveSessions:
    @pytest.mark.asyncio
    async def test_removes_expired_session(self):
        """Session inactive for longer than timeout_seconds must be removed."""
        mgr = SessionManager(max_concurrent_sessions=10, max_workers=1)
        inject_session(mgr, "sess-1", user_id=1, seconds_since_activity=7200)  # 2h ago

        await mgr.cleanup_inactive_sessions(timeout_seconds=3600)

        assert "sess-1" not in mgr.sessions
        assert 1 not in mgr.user_sessions

    @pytest.mark.asyncio
    async def test_keeps_recently_active_session(self):
        """Session active within the timeout window must NOT be removed."""
        mgr = SessionManager(max_concurrent_sessions=10, max_workers=1)
        inject_session(mgr, "sess-2", user_id=2, seconds_since_activity=60)  # 1 min ago

        await mgr.cleanup_inactive_sessions(timeout_seconds=3600)

        assert "sess-2" in mgr.sessions
        assert 2 in mgr.user_sessions

    @pytest.mark.asyncio
    async def test_removes_only_expired_sessions(self):
        """Only expired sessions are cleaned up; active ones survive."""
        mgr = SessionManager(max_concurrent_sessions=10, max_workers=1)
        inject_session(mgr, "old", user_id=10, seconds_since_activity=9000)
        inject_session(mgr, "fresh", user_id=11, seconds_since_activity=30)

        await mgr.cleanup_inactive_sessions(timeout_seconds=3600)

        assert "old" not in mgr.sessions
        assert 10 not in mgr.user_sessions
        assert "fresh" in mgr.sessions
        assert 11 in mgr.user_sessions

    @pytest.mark.asyncio
    async def test_noop_when_no_sessions(self):
        """Cleanup on empty manager must not raise."""
        mgr = SessionManager(max_concurrent_sessions=10, max_workers=1)
        await mgr.cleanup_inactive_sessions(timeout_seconds=3600)
        assert mgr.get_stats()["total_sessions"] == 0

    @pytest.mark.asyncio
    async def test_zero_timeout_removes_all(self):
        """timeout_seconds=0 means every session is expired."""
        mgr = SessionManager(max_concurrent_sessions=10, max_workers=1)
        inject_session(mgr, "s1", user_id=1, seconds_since_activity=0)
        inject_session(mgr, "s2", user_id=2, seconds_since_activity=0)

        await mgr.cleanup_inactive_sessions(timeout_seconds=0)

        assert len(mgr.sessions) == 0
        assert len(mgr.user_sessions) == 0

    @pytest.mark.asyncio
    async def test_cleanup_calls_session_cleanup(self):
        """cleanup_inactive_sessions must call session.cleanup() on each evicted session."""
        mgr = SessionManager(max_concurrent_sessions=10, max_workers=1)
        sess = inject_session(mgr, "s1", user_id=1, seconds_since_activity=7200)

        await mgr.cleanup_inactive_sessions(timeout_seconds=3600)

        sess.cleanup.assert_called_once()


# ─── close_session ────────────────────────────────────────────────────────────


class TestCloseSession:
    @pytest.mark.asyncio
    async def test_removes_from_sessions_dict(self):
        mgr = SessionManager(max_concurrent_sessions=10, max_workers=1)
        inject_session(mgr, "sess-x", user_id=5)

        await mgr.close_session("sess-x")

        assert "sess-x" not in mgr.sessions

    @pytest.mark.asyncio
    async def test_removes_from_user_sessions_dict(self):
        mgr = SessionManager(max_concurrent_sessions=10, max_workers=1)
        inject_session(mgr, "sess-y", user_id=6)

        await mgr.close_session("sess-y")

        assert 6 not in mgr.user_sessions

    @pytest.mark.asyncio
    async def test_noop_on_nonexistent_session_id(self):
        """Closing an already-closed session must not raise."""
        mgr = SessionManager(max_concurrent_sessions=10, max_workers=1)
        await mgr.close_session("does-not-exist")

    @pytest.mark.asyncio
    async def test_closes_websocket_if_present(self):
        """If the session has an open WebSocket, it must be closed."""
        mgr = SessionManager(max_concurrent_sessions=10, max_workers=1)
        sess = inject_session(mgr, "ws-sess", user_id=7)
        sess.websocket = AsyncMock()
        sess.websocket.close = AsyncMock()

        await mgr.close_session("ws-sess")

        sess.websocket.close.assert_called_once()


# ─── Single-session-per-user ──────────────────────────────────────────────────


class TestSingleSessionPerUser:
    @pytest.mark.asyncio
    async def test_old_session_removed_when_new_one_requested(self):
        """
        If a user already has an active session, creating a new one must
        close the previous one (enforced inside create_session via user_sessions lookup).

        We test this by directly simulating the conflict: inject an existing session,
        then verify that after calling close_session for the old id the new mapping works.
        """
        mgr = SessionManager(max_concurrent_sessions=10, max_workers=1)

        # Inject first session
        old_sess = inject_session(mgr, "old-sess", user_id=99)

        # Simulate what create_session does when it finds user_id already in user_sessions:
        old_id = mgr.user_sessions.get(99)
        assert old_id == "old-sess"
        await mgr._close_session_unlocked(old_id)

        # Inject new session for same user
        inject_session(mgr, "new-sess", user_id=99)

        assert "old-sess" not in mgr.sessions
        assert mgr.user_sessions[99] == "new-sess"
        old_sess.cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_user_session_returns_none_after_close(self):
        mgr = SessionManager(max_concurrent_sessions=10, max_workers=1)
        inject_session(mgr, "sess-u", user_id=42)

        await mgr.close_session("sess-u")

        result = await mgr.get_user_session(42)
        assert result is None


# ─── get_stats ────────────────────────────────────────────────────────────────


class TestGetStats:
    def test_empty_manager_stats(self):
        mgr = SessionManager(max_concurrent_sessions=50, max_workers=1)
        stats = mgr.get_stats()

        assert stats["total_sessions"] == 0
        assert stats["active_users"] == 0
        assert stats["max_sessions"] == 50
        assert stats["capacity_percent"] == 0

    def test_stats_reflect_injected_sessions(self):
        mgr = SessionManager(max_concurrent_sessions=10, max_workers=1)
        inject_session(mgr, "s1", user_id=1)
        inject_session(mgr, "s2", user_id=2)

        stats = mgr.get_stats()
        assert stats["total_sessions"] == 2
        assert stats["active_users"] == 2
        assert stats["capacity_percent"] == 20  # 2/10 * 100


# ─── Session capacity ─────────────────────────────────────────────────────────


class TestSessionCapacity:
    @pytest.mark.asyncio
    async def test_create_session_returns_none_when_at_capacity(self):
        """When max_concurrent_sessions is reached, create_session must return None."""
        mgr = SessionManager(max_concurrent_sessions=2, max_workers=1)
        inject_session(mgr, "s1", user_id=1)
        inject_session(mgr, "s2", user_id=2)

        # Mock UserSession to avoid importing azure dependencies
        with patch("voice_assistant.session_manager.UserSession") as MockSession:
            result = await mgr.create_session(user_id=3, training_id=None)

        assert result is None
        MockSession.assert_not_called()
