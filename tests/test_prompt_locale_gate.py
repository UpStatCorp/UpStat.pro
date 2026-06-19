"""
Тесты строгого locale-gate для AI-поведения.

Критичное требование: локализация НЕ должна менять поведение ИИ для
существующих RU-train-пользователей. EN-контент (системный промпт и этапные
.txt) применяется ТОЛЬКО если он явно создан; иначе используется текущий RU.
"""

import importlib

import pytest


class TestSystemPromptGate:
    def _config(self, monkeypatch, en_value=None):
        if en_value is None:
            monkeypatch.delenv("SYSTEM_PROMPT_EN", raising=False)
        else:
            monkeypatch.setenv("SYSTEM_PROMPT_EN", en_value)
        from voice_assistant import config

        return importlib.reload(config)

    def test_ru_returns_ru_prompt(self, monkeypatch):
        cfg = self._config(monkeypatch)
        assert cfg.get_system_prompt("ru") == cfg.SYSTEM_PROMPT

    def test_en_without_variant_returns_ru_prompt(self, monkeypatch):
        # Главный гейт: нет EN-варианта → RU-промпт, поведение не меняется
        cfg = self._config(monkeypatch)
        assert cfg.SYSTEM_PROMPT_EN is None
        assert cfg.get_system_prompt("en") == cfg.SYSTEM_PROMPT

    def test_en_with_variant_returns_en_prompt(self, monkeypatch):
        cfg = self._config(monkeypatch, en_value="You are a sales training coach.")
        assert cfg.get_system_prompt("en") == "You are a sales training coach."
        # RU всё равно остаётся RU
        assert cfg.get_system_prompt("ru") == cfg.SYSTEM_PROMPT

    def test_default_arg_is_ru(self, monkeypatch):
        cfg = self._config(monkeypatch)
        assert cfg.get_system_prompt() == cfg.SYSTEM_PROMPT


class TestStagesLocaleGate:
    """load_stages: EN-подпапка применяется только при наличии файлов."""

    def test_ru_loads_base_stages(self):
        from services.training_stages_service import load_stages

        stages = load_stages("closing", locale="ru")
        assert len(stages) > 0

    def test_en_without_subfolder_falls_back_to_ru(self):
        from services.training_stages_service import load_stages

        ru = load_stages("closing", locale="ru")
        en = load_stages("closing", locale="en")
        # Нет папки closing/en → идентичные промпты, поведение не меняется
        assert [s.prompt for s in en] == [s.prompt for s in ru]

    def test_unknown_stage_returns_empty(self):
        from services.training_stages_service import load_stages

        assert load_stages("does_not_exist_xyz", locale="en") == []

    def test_none_stage_returns_empty(self):
        from services.training_stages_service import load_stages

        assert load_stages(None, locale="en") == []

    def test_en_subfolder_used_when_present(self, tmp_path, monkeypatch):
        import services.training_stages_service as svc

        # Подменяем корень тренировок на временную папку с en/ вариантом
        root = tmp_path / "trainings"
        (root / "demo").mkdir(parents=True)
        (root / "demo" / "stage_1.txt").write_text("РУССКИЙ промпт этапа 1", encoding="utf-8")
        (root / "demo" / "en").mkdir()
        (root / "demo" / "en" / "stage_1.txt").write_text("ENGLISH stage 1 prompt", encoding="utf-8")

        monkeypatch.setattr(svc, "_trainings_root", lambda: root)

        ru = svc.load_stages("demo", locale="ru")
        en = svc.load_stages("demo", locale="en")
        assert "РУССКИЙ" in ru[0].prompt
        assert "ENGLISH" in en[0].prompt
