from __future__ import annotations

import logging

import pytest

from app import logging_config


@pytest.fixture(autouse=True)
def reset_logging_state() -> None:
    logging_config.reset_for_tests()
    yield
    logging_config.reset_for_tests()


def test_resolve_level_uses_env_when_no_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "warning")
    assert logging_config._resolve_level(None) == "WARNING"


def test_resolve_level_falls_back_when_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "bogus")
    assert logging_config._resolve_level(None) == "INFO"


def test_resolve_level_explicit_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    assert logging_config._resolve_level("debug") == "DEBUG"


def test_resolve_level_defaults_to_info_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    assert logging_config._resolve_level(None) == "INFO"


def test_build_config_shape() -> None:
    cfg = logging_config.build_config("DEBUG")
    assert cfg["version"] == 1
    assert cfg["disable_existing_loggers"] is False
    assert cfg["root"]["level"] == "DEBUG"
    assert cfg["root"]["handlers"] == ["stderr"]
    assert cfg["loggers"]["app"]["level"] == "DEBUG"
    assert cfg["loggers"]["uvicorn"]["propagate"] is False
    assert cfg["loggers"]["sqlalchemy.engine"]["level"] == "WARNING"
    assert cfg["loggers"]["passlib"]["level"] == "ERROR"


def test_configure_logging_attaches_root_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    logging_config.configure_logging()
    root = logging.getLogger()
    handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
    assert handlers, "root logger has no StreamHandler after configure_logging"


def test_configure_logging_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    logging_config.configure_logging()
    handler_count_after_first = len(logging.getLogger().handlers)
    logging_config.configure_logging()
    logging_config.configure_logging()
    assert len(logging.getLogger().handlers) == handler_count_after_first


def test_configure_logging_explicit_level_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    logging_config.configure_logging(level="debug")
    assert logging.getLogger("app").getEffectiveLevel() == logging.DEBUG


def test_app_logger_emits_info_after_configure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    logging_config.configure_logging()
    logging.getLogger("app.unit_test").info("hello-from-app")
    for handler in logging.getLogger().handlers:
        handler.flush()
    captured = capsys.readouterr()
    assert "hello-from-app" in captured.err
    assert "app.unit_test" in captured.err


def test_reset_for_tests_allows_reconfigure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    logging_config.configure_logging()
    logging_config.reset_for_tests()
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    logging_config.configure_logging()
    assert logging.getLogger("app").getEffectiveLevel() == logging.DEBUG
