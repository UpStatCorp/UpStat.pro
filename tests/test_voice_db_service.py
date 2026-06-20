"""
Unit-тесты для voice-слоя БД и фоновых сохранений.

Покрывает ключевые правки прод-харднинга:
- abort_training_session НЕ перетирает завершённую сессию (раздел B плана)
- build_transcript форматирует серверный транскрипт и пропускает пустые реплики (раздел C)
- _fire_save / _drain_saves трекают и дожидаются фоновых записей (гонка последней
  реплики, раздел C)
"""

import sys
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from voice_assistant.db_service import VoiceTrainingDBService


@pytest.fixture(autouse=True)
def _mock_models():
    """models/app.models → MagicMock, чтобы не требовался реальный PostgreSQL."""
    mock_models = MagicMock()
    with patch.dict(sys.modules, {"models": mock_models, "app.models": mock_models}):
        yield


# ─── abort guard (раздел B) ───────────────────────────────────────────────────


class TestAbortGuard:
    def test_does_not_overwrite_completed(self):
        db = MagicMock()
        sess = MagicMock()
        sess.status = "completed"
        db.query.return_value.filter.return_value.first.return_value = sess

        VoiceTrainingDBService.abort_training_session(db, 1)

        assert sess.status == "completed"  # НЕ перетёрто на aborted
        db.commit.assert_not_called()

    def test_does_not_overwrite_aborted(self):
        db = MagicMock()
        sess = MagicMock()
        sess.status = "aborted"
        db.query.return_value.filter.return_value.first.return_value = sess

        VoiceTrainingDBService.abort_training_session(db, 1)

        assert sess.status == "aborted"
        db.commit.assert_not_called()

    def test_marks_active_as_aborted(self):
        db = MagicMock()
        sess = MagicMock()
        sess.status = "active"
        sess.started_at = None
        db.query.return_value.filter.return_value.first.return_value = sess

        VoiceTrainingDBService.abort_training_session(db, 1)

        assert sess.status == "aborted"
        db.commit.assert_called_once()

    def test_marks_in_progress_as_aborted(self):
        db = MagicMock()
        sess = MagicMock()
        sess.status = "in_progress"
        sess.started_at = None
        db.query.return_value.filter.return_value.first.return_value = sess

        VoiceTrainingDBService.abort_training_session(db, 1)

        assert sess.status == "aborted"
        db.commit.assert_called_once()

    def test_missing_session_is_noop(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        VoiceTrainingDBService.abort_training_session(db, 1)

        db.commit.assert_not_called()


# ─── build_transcript (раздел C) ──────────────────────────────────────────────


class TestBuildTranscript:
    @staticmethod
    def _msg(role, text):
        m = MagicMock()
        m.role = role
        m.text = text
        return m

    def test_formats_roles_and_skips_empty(self):
        db = MagicMock()
        rows = [
            self._msg("user", " Привет "),
            self._msg("assistant", "Здравствуйте"),
            self._msg("user", "   "),  # пустая после strip — пропускаем
        ]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = rows

        out = VoiceTrainingDBService.build_transcript(db, 1)

        assert out == "Менеджер: Привет\nТренер: Здравствуйте"

    def test_empty_session_returns_empty_string(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        assert VoiceTrainingDBService.build_transcript(db, 1) == ""


# ─── _fire_save / _drain_saves (гонка последней реплики, раздел C) ─────────────


class TestSaveDrain:
    @pytest.mark.asyncio
    async def test_fire_save_tracks_and_drain_awaits(self):
        from voice_assistant import websocket_handler as wh

        us = MagicMock()
        us.db_session_id = 5
        us.pending_saves = set()

        with patch.object(wh, "run_db", new_callable=AsyncMock) as mock_run:
            wh._fire_save(us, "user", "привет")
            assert len(us.pending_saves) == 1  # задача зарегистрирована

            await wh._drain_saves(us)
            await asyncio.sleep(0)  # дать done-callback'ам отработать

            mock_run.assert_awaited_once()

        assert len(us.pending_saves) == 0  # после дренажа множество пустое

    @pytest.mark.asyncio
    async def test_fire_save_noop_without_db_session(self):
        from voice_assistant import websocket_handler as wh

        us = MagicMock()
        us.db_session_id = None
        us.pending_saves = set()

        with patch.object(wh, "run_db", new_callable=AsyncMock) as mock_run:
            wh._fire_save(us, "user", "привет")

        assert len(us.pending_saves) == 0
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_drain_empty_is_safe(self):
        from voice_assistant import websocket_handler as wh

        us = MagicMock()
        us.pending_saves = set()
        await wh._drain_saves(us)  # не должно падать
