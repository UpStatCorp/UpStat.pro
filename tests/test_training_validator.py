"""
Unit tests for app/services/training_validator_service.py.

Covers:
- Short/empty transcript → immediate score=0 without calling OpenAI
- Happy path → score clamped 0-100, passed=True when score≥70
- Transient errors (APITimeoutError, APIConnectionError, etc.) → 3 retries with backoff,
  then raise ValidationTransientError
- Non-transient errors (ValueError, JSONDecodeError, etc.) → immediate score=0, no retry
- validate_and_complete_training: ValidationTransientError → no DB commit, validation_error=True
- validate_and_complete_training: happy path → session marked completed, DB commit
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from openai import APITimeoutError, APIConnectionError, RateLimitError

from services.training_validator_service import (
    TrainingValidatorService,
    ValidationTransientError,
    _TRANSIENT,
    _DEFAULT_MODEL,
    _EMPTY_CRITERIA,
)


@pytest.fixture(autouse=True)
def _reset_validator_client():
    """Сбрасываем кэш переиспользуемого AsyncOpenAI-клиента между тестами —
    иначе закэшированный клиент игнорировал бы patch('...AsyncOpenAI')."""
    import services.training_validator_service as _svc
    _svc._client = None
    yield
    _svc._client = None


# ─── Helpers ──────────────────────────────────────────────────────────────────

LONG_TRANSCRIPT = "Manager: Hello. Client: Hi. " * 20  # > 50 chars

VALID_RESPONSE_JSON = json.dumps({
    "score": 85,
    "passed": True,
    "criteria": {
        "full_cycle": 20,
        "understanding": 22,
        "execution_quality": 21,
        "active_participation": 22,
    },
    "feedback": "Отличная тренировка!",
    "details": "Менеджер правильно прошёл все этапы.",
})


def _make_openai_response(content: str, prompt_tokens: int = 100, completion_tokens: int = 50):
    """Build a minimal mock OpenAI chat completion response."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.usage = MagicMock()
    response.usage.prompt_tokens = prompt_tokens
    response.usage.completion_tokens = completion_tokens
    return response


def _mock_client(create_return=None, create_side_effect=None):
    """Return a (mock_cls, mock_instance) pair for patching AsyncOpenAI."""
    mock_instance = MagicMock()
    if create_side_effect is not None:
        mock_instance.chat.completions.create = AsyncMock(side_effect=create_side_effect)
    else:
        mock_instance.chat.completions.create = AsyncMock(return_value=create_return)
    mock_cls = MagicMock(return_value=mock_instance)
    return mock_cls, mock_instance


# ─── validate_training: short transcript ──────────────────────────────────────


class TestShortTranscript:
    @pytest.mark.asyncio
    async def test_empty_transcript_returns_zero(self):
        result = await TrainingValidatorService.validate_training(
            transcript="",
            training_title="T",
            training_description="D",
            training_recommendation="R",
        )
        assert result["score"] == 0
        assert result["passed"] is False

    @pytest.mark.asyncio
    async def test_too_short_transcript_returns_zero(self):
        result = await TrainingValidatorService.validate_training(
            transcript="Hi",  # < 50 chars
            training_title="T",
            training_description="D",
            training_recommendation="R",
        )
        assert result["score"] == 0

    @pytest.mark.asyncio
    async def test_short_transcript_does_not_call_openai(self):
        mock_cls, _ = _mock_client(create_return=MagicMock())
        with patch("services.training_validator_service.get_async_llm_client", mock_cls):
            await TrainingValidatorService.validate_training(
                transcript="short",
                training_title="T",
                training_description="D",
                training_recommendation="R",
            )
        mock_cls.assert_not_called()


# ─── validate_training: happy path ────────────────────────────────────────────


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_returns_score_from_openai(self):
        response = _make_openai_response(VALID_RESPONSE_JSON)
        mock_cls, _ = _mock_client(create_return=response)

        with patch("services.training_validator_service.get_async_llm_client", mock_cls):
            result = await TrainingValidatorService.validate_training(
                transcript=LONG_TRANSCRIPT,
                training_title="Test",
                training_description="Desc",
                training_recommendation="Rec",
            )

        assert result["score"] == 85
        assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_score_clamped_to_100(self):
        """Score above 100 must be clamped down to 100."""
        over_json = json.dumps({"score": 150, "passed": True, "criteria": {}, "feedback": "", "details": ""})
        response = _make_openai_response(over_json)
        mock_cls, _ = _mock_client(create_return=response)

        with patch("services.training_validator_service.get_async_llm_client", mock_cls):
            result = await TrainingValidatorService.validate_training(
                transcript=LONG_TRANSCRIPT,
                training_title="T",
                training_description="D",
                training_recommendation="R",
            )
        assert result["score"] == 100

    @pytest.mark.asyncio
    async def test_score_clamped_to_zero(self):
        """Score below 0 must be clamped up to 0."""
        neg_json = json.dumps({"score": -10, "passed": False, "criteria": {}, "feedback": "", "details": ""})
        response = _make_openai_response(neg_json)
        mock_cls, _ = _mock_client(create_return=response)

        with patch("services.training_validator_service.get_async_llm_client", mock_cls):
            result = await TrainingValidatorService.validate_training(
                transcript=LONG_TRANSCRIPT,
                training_title="T",
                training_description="D",
                training_recommendation="R",
            )
        assert result["score"] == 0

    @pytest.mark.asyncio
    async def test_passed_false_when_score_below_70(self):
        low_json = json.dumps({"score": 65, "passed": True, "criteria": {}, "feedback": "", "details": ""})
        response = _make_openai_response(low_json)
        mock_cls, _ = _mock_client(create_return=response)

        with patch("services.training_validator_service.get_async_llm_client", mock_cls):
            result = await TrainingValidatorService.validate_training(
                transcript=LONG_TRANSCRIPT,
                training_title="T",
                training_description="D",
                training_recommendation="R",
            )
        assert result["passed"] is False  # validator must override what GPT says

    @pytest.mark.asyncio
    async def test_uses_env_model(self, monkeypatch):
        monkeypatch.setenv("OPENAI_VALIDATOR_MODEL", "gpt-4o")
        response = _make_openai_response(VALID_RESPONSE_JSON)
        mock_cls, mock_instance = _mock_client(create_return=response)

        with patch("services.training_validator_service.get_async_llm_client", mock_cls):
            await TrainingValidatorService.validate_training(
                transcript=LONG_TRANSCRIPT,
                training_title="T",
                training_description="D",
                training_recommendation="R",
            )

        create_call_kwargs = mock_instance.chat.completions.create.call_args
        assert create_call_kwargs.kwargs["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_default_model_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("OPENAI_VALIDATOR_MODEL", raising=False)
        response = _make_openai_response(VALID_RESPONSE_JSON)
        mock_cls, mock_instance = _mock_client(create_return=response)

        with patch("services.training_validator_service.get_async_llm_client", mock_cls):
            await TrainingValidatorService.validate_training(
                transcript=LONG_TRANSCRIPT,
                training_title="T",
                training_description="D",
                training_recommendation="R",
            )

        create_call_kwargs = mock_instance.chat.completions.create.call_args
        assert create_call_kwargs.kwargs["model"] == _DEFAULT_MODEL

    @pytest.mark.asyncio
    async def test_timeout_60s(self):
        response = _make_openai_response(VALID_RESPONSE_JSON)
        mock_cls, mock_instance = _mock_client(create_return=response)

        with patch("services.training_validator_service.get_async_llm_client", mock_cls):
            await TrainingValidatorService.validate_training(
                transcript=LONG_TRANSCRIPT,
                training_title="T",
                training_description="D",
                training_recommendation="R",
            )

        create_kwargs = mock_instance.chat.completions.create.call_args.kwargs
        assert create_kwargs.get("timeout") == 60.0


# ─── validate_training: transient errors ──────────────────────────────────────


class TestTransientErrors:
    @pytest.mark.asyncio
    async def test_all_three_transient_raises_validation_transient_error(self):
        """3 consecutive transient errors → ValidationTransientError (not regular Exception)."""
        side_effects = [
            APITimeoutError("t1"),
            APITimeoutError("t2"),
            APITimeoutError("t3"),
        ]
        mock_cls, mock_instance = _mock_client(create_side_effect=side_effects)

        with patch("services.training_validator_service.get_async_llm_client", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ValidationTransientError):
                await TrainingValidatorService.validate_training(
                    transcript=LONG_TRANSCRIPT,
                    training_title="T",
                    training_description="D",
                    training_recommendation="R",
                )

        assert mock_instance.chat.completions.create.call_count == 3

    @pytest.mark.asyncio
    async def test_retry_exactly_three_times(self):
        side_effects = [APIConnectionError(request=MagicMock())] * 3
        mock_cls, mock_instance = _mock_client(create_side_effect=side_effects)

        with patch("services.training_validator_service.get_async_llm_client", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ValidationTransientError):
                await TrainingValidatorService.validate_training(
                    transcript=LONG_TRANSCRIPT,
                    training_title="T",
                    training_description="D",
                    training_recommendation="R",
                )

        # Must be exactly 3 attempts, no more
        assert mock_instance.chat.completions.create.call_count == 3

    @pytest.mark.asyncio
    async def test_backoff_delays_are_2s_then_4s(self):
        """Delays must be 2s (after attempt 1) and 4s (after attempt 2)."""
        side_effects = [APITimeoutError("t")] * 3
        mock_cls, _ = _mock_client(create_side_effect=side_effects)

        with patch("services.training_validator_service.get_async_llm_client", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(ValidationTransientError):
                await TrainingValidatorService.validate_training(
                    transcript=LONG_TRANSCRIPT,
                    training_title="T",
                    training_description="D",
                    training_recommendation="R",
                )

        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0] == call(2)
        assert mock_sleep.call_args_list[1] == call(4)

    @pytest.mark.asyncio
    async def test_rate_limit_error_also_retries(self):
        """RateLimitError is transient and must trigger retry."""
        side_effects = [RateLimitError(message="rate limited", response=MagicMock(), body={})] * 3
        mock_cls, mock_instance = _mock_client(create_side_effect=side_effects)

        with patch("services.training_validator_service.get_async_llm_client", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ValidationTransientError):
                await TrainingValidatorService.validate_training(
                    transcript=LONG_TRANSCRIPT,
                    training_title="T",
                    training_description="D",
                    training_recommendation="R",
                )

        assert mock_instance.chat.completions.create.call_count == 3

    @pytest.mark.asyncio
    async def test_succeeds_on_third_attempt(self):
        """If third attempt succeeds, no error is raised."""
        response = _make_openai_response(VALID_RESPONSE_JSON)
        side_effects = [
            APITimeoutError("t1"),
            APITimeoutError("t2"),
            response,
        ]
        mock_cls, mock_instance = _mock_client(create_side_effect=side_effects)

        with patch("services.training_validator_service.get_async_llm_client", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await TrainingValidatorService.validate_training(
                transcript=LONG_TRANSCRIPT,
                training_title="T",
                training_description="D",
                training_recommendation="R",
            )

        assert result["score"] == 85
        assert mock_instance.chat.completions.create.call_count == 3
        assert mock_sleep.call_count == 2  # slept twice before attempt 3


# ─── validate_training: non-transient errors ──────────────────────────────────


class TestNonTransientErrors:
    @pytest.mark.asyncio
    async def test_value_error_returns_score_zero_immediately(self):
        """Non-transient errors must return score=0 without retry."""
        mock_cls, mock_instance = _mock_client(create_side_effect=ValueError("bad config"))

        with patch("services.training_validator_service.get_async_llm_client", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await TrainingValidatorService.validate_training(
                transcript=LONG_TRANSCRIPT,
                training_title="T",
                training_description="D",
                training_recommendation="R",
            )

        assert result["score"] == 0
        assert result["passed"] is False
        assert mock_instance.chat.completions.create.call_count == 1
        assert mock_sleep.call_count == 0

    @pytest.mark.asyncio
    async def test_json_decode_error_returns_score_zero(self):
        """JSONDecodeError (bad GPT output) is non-transient — score=0, no retry."""
        bad_response = _make_openai_response("not-json-at-all{{{")
        mock_cls, mock_instance = _mock_client(create_return=bad_response)

        with patch("services.training_validator_service.get_async_llm_client", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await TrainingValidatorService.validate_training(
                transcript=LONG_TRANSCRIPT,
                training_title="T",
                training_description="D",
                training_recommendation="R",
            )

        assert result["score"] == 0
        assert mock_instance.chat.completions.create.call_count == 1
        assert mock_sleep.call_count == 0

    @pytest.mark.asyncio
    async def test_non_transient_response_does_not_leak_error_details(self):
        """Client-facing details must not include internal error messages."""
        mock_cls, _ = _mock_client(create_side_effect=ValueError("INTERNAL: key=sk-xxx..."))

        with patch("services.training_validator_service.get_async_llm_client", mock_cls):
            result = await TrainingValidatorService.validate_training(
                transcript=LONG_TRANSCRIPT,
                training_title="T",
                training_description="D",
                training_recommendation="R",
            )

        assert "sk-xxx" not in result.get("feedback", "")
        assert "sk-xxx" not in result.get("details", "")
        assert "INTERNAL" not in result.get("feedback", "")
        assert "INTERNAL" not in result.get("details", "")


# ─── validate_and_complete_training ───────────────────────────────────────────


class TestValidateAndCompleteTraining:
    @pytest.fixture(autouse=True)
    def mock_db_imports(self):
        """
        Prevent app/database.py from raising ValueError (DATABASE_URL not set).
        Patches sys.modules so the lazy imports inside validate_and_complete_training
        get MagicMock objects instead of trying to connect to PostgreSQL.
        """
        import sys

        mock_database = MagicMock()
        mock_models = MagicMock()
        mock_plan_svc = MagicMock()

        modules = {
            "database": mock_database,
            "models": mock_models,
            "app.database": mock_database,
            "app.models": mock_models,
            "services.training_plan_service": mock_plan_svc,
            "app.services.training_plan_service": mock_plan_svc,
        }
        with patch.dict(sys.modules, modules):
            yield

    def _make_db_with_training(self, training_id=1, title="T", description="D", recommendation="R", stage=None):
        db = MagicMock()
        training = MagicMock()
        training.id = training_id
        training.title = title
        training.description = description
        training.recommendation = recommendation
        training.stage = stage
        training.best_score = None
        training.plan = None

        session = MagicMock()
        session.id = 999
        session.started_at = None
        session.duration_seconds = None

        # Порядок filter_by().first(): training (validate_and_complete) →
        # training (persist_validation) → session (persist_validation).
        db.query.return_value.filter_by.return_value.first.side_effect = [training, training, session]
        return db, training, session

    @pytest.mark.asyncio
    async def test_transient_error_no_db_commit(self):
        """On ValidationTransientError, DB must NOT be committed and validation_error=True."""
        db, _, _ = self._make_db_with_training()

        with patch.object(
            TrainingValidatorService,
            "validate_training",
            new_callable=AsyncMock,
            side_effect=ValidationTransientError("infra failure"),
        ):
            result = await TrainingValidatorService.validate_and_complete_training(
                db=db,
                session_id=999,
                training_id=1,
                transcript=LONG_TRANSCRIPT,
            )

        db.commit.assert_not_called()
        assert result["validation_error"] is True
        assert result["score"] == 0
        assert result["passed"] is False
        assert result["training_completed"] is False

    @pytest.mark.asyncio
    async def test_transient_error_returns_russian_user_message(self):
        db, _, _ = self._make_db_with_training()

        with patch.object(
            TrainingValidatorService,
            "validate_training",
            new_callable=AsyncMock,
            side_effect=ValidationTransientError("timeout"),
        ):
            result = await TrainingValidatorService.validate_and_complete_training(
                db=db,
                session_id=999,
                training_id=1,
                transcript=LONG_TRANSCRIPT,
            )

        # Must have a human-readable feedback in Russian (not an internal error message)
        assert result.get("feedback")
        assert len(result["feedback"]) > 10

    @pytest.mark.asyncio
    async def test_happy_path_commits_db(self):
        """On successful validation, DB commit must be called."""
        db, training, session = self._make_db_with_training()
        # Reset side_effect: training (validate_and_complete) → training + session (persist)
        db.query.return_value.filter_by.return_value.first.side_effect = [training, training, session]

        validation_result = {
            "score": 80,
            "passed": True,
            "criteria": _EMPTY_CRITERIA.copy(),
            "feedback": "Хорошо!",
            "details": "Детали.",
        }

        with patch.object(
            TrainingValidatorService,
            "validate_training",
            new_callable=AsyncMock,
            return_value=validation_result,
        ):
            result = await TrainingValidatorService.validate_and_complete_training(
                db=db,
                session_id=999,
                training_id=1,
                transcript=LONG_TRANSCRIPT,
            )

        db.commit.assert_called_once()
        assert result.get("validation_error") is None or result.get("validation_error") is False
        assert result["score"] == 80
        assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_missing_training_returns_error_without_crash(self):
        """If training_id doesn't exist, return graceful error dict."""
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None

        result = await TrainingValidatorService.validate_and_complete_training(
            db=db,
            session_id=999,
            training_id=9999,
            transcript=LONG_TRANSCRIPT,
        )

        assert result["score"] == 0
        assert result["training_completed"] is False
        db.commit.assert_not_called()

    def test_persist_no_double_increment_when_already_completed(self):
        """Идемпотентность плана: если тренировка уже была completed, повторный
        passed-результат НЕ инкрементит plan.completed_trainings (гонка авто+кнопка)."""
        db = MagicMock()
        training = MagicMock()
        training.best_score = 50
        training.status = "completed"  # уже завершена ранее
        plan = MagicMock()
        plan.completed_trainings = 1
        plan.total_trainings = 3
        plan.status = "active"
        training.plan = plan
        session = MagicMock()
        session.started_at = None
        session.duration_seconds = None
        session.user_id = 7
        db.query.return_value.filter_by.return_value.first.side_effect = [training, session]

        vr = {"score": 90, "passed": True, "criteria": _EMPTY_CRITERIA.copy(), "feedback": "ok", "details": "d"}
        TrainingValidatorService.persist_validation(db, 999, 1, vr)

        assert plan.completed_trainings == 1  # НЕ увеличился


# ─── Server transcript & tail-keeping truncation ──────────────────────────────


class TestServerTranscriptAndTruncation:
    @pytest.mark.asyncio
    async def test_truncation_keeps_tail_not_head(self):
        """Длинный транскрипт усекается с сохранением ХВОСТА (концовка важнее)."""
        from services import training_validator_service as svc

        head = "HEAD_MARKER_должна_исчезнуть "
        tail = " TAIL_MARKER_должна_остаться"
        big = head + ("ё" * (svc._MAX_TRANSCRIPT_CHARS)) + tail

        response = _make_openai_response(VALID_RESPONSE_JSON)
        mock_cls, mock_instance = _mock_client(create_return=response)
        with patch("services.training_validator_service.get_async_llm_client", mock_cls):
            await TrainingValidatorService.validate_training(
                transcript=big,
                training_title="T",
                training_description="D",
                training_recommendation="R",
            )

        sent = mock_instance.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        assert "TAIL_MARKER" in sent
        assert "HEAD_MARKER" not in sent

    @pytest.mark.asyncio
    async def test_resolve_transcript_prefers_server(self):
        """Серверный транскрипт из БД имеет приоритет над клиентским (анти-подделка)."""
        # Патчим живой объект класса (import гарантирует ту же запись в sys.modules,
        # что и ленивый импорт внутри _resolve_transcript — устойчиво к перезагрузке
        # модуля другими тестами через patch.dict(sys.modules)).
        import voice_assistant.db_service as _dbs
        db = MagicMock()
        with patch.object(_dbs.VoiceTrainingDBService, "build_transcript", return_value="SERVER_SIDE"):
            out = await TrainingValidatorService._resolve_transcript(db, 1, "CLIENT_FAKE")
        assert out == "SERVER_SIDE"

    @pytest.mark.asyncio
    async def test_resolve_transcript_falls_back_to_client_when_db_empty(self):
        """Если в БД нет реплик — fallback на клиентский транскрипт (не голосовая сессия)."""
        import voice_assistant.db_service as _dbs
        db = MagicMock()
        with patch.object(_dbs.VoiceTrainingDBService, "build_transcript", return_value=""), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            out = await TrainingValidatorService._resolve_transcript(db, 1, "CLIENT_FALLBACK")
        assert out == "CLIENT_FALLBACK"

    @pytest.mark.asyncio
    async def test_resolve_transcript_retries_when_fewer_than_expected(self):
        """Гонка последней реплики: серверных реплик меньше, чем заявил клиент →
        одна повторная попытка (фоновое сохранение успевает закоммититься)."""
        import voice_assistant.db_service as _dbs
        db = MagicMock()
        # 1-й вызов: 1 реплика; 2-й: 2 реплики (последняя долетела)
        with patch.object(
            _dbs.VoiceTrainingDBService,
            "build_transcript",
            side_effect=["Менеджер: a", "Менеджер: a\nТренер: b"],
        ) as m, patch("asyncio.sleep", new_callable=AsyncMock):
            out = await TrainingValidatorService._resolve_transcript(
                db, 1, "client", expected_messages=2
            )
        assert m.call_count == 2  # был ретрай
        assert out == "Менеджер: a\nТренер: b"

    @pytest.mark.asyncio
    async def test_resolve_transcript_no_retry_when_count_satisfied(self):
        """Если серверных реплик достаточно — повторного чтения нет."""
        import voice_assistant.db_service as _dbs
        db = MagicMock()
        with patch.object(
            _dbs.VoiceTrainingDBService,
            "build_transcript",
            return_value="Менеджер: a\nТренер: b",
        ) as m, patch("asyncio.sleep", new_callable=AsyncMock):
            out = await TrainingValidatorService._resolve_transcript(
                db, 1, "client", expected_messages=2
            )
        assert m.call_count == 1  # ретрая не было
        assert out == "Менеджер: a\nТренер: b"
