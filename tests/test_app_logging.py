"""Tests for persistent rotating log file configuration and log tail reading."""

from __future__ import annotations

import logging
from pathlib import Path

from src.app_logging import (
    LOG_FILE_NAME,
    RateLimitedLog,
    configure_logging,
    get_app_data_dir,
    read_log_tail,
    shutdown_logging,
)


def test_get_app_data_dir_uses_localappdata(monkeypatch, tmp_path: Path) -> None:
    """Runtime data lives under %LOCALAPPDATA%\\FastCon, never next to source code."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert get_app_data_dir() == tmp_path / "FastCon"


def test_configure_logging_writes_utf8_file_and_silences_httpx(tmp_path: Path) -> None:
    """Log records (including Turkish characters) reach the rotating UTF-8 log file."""
    log_file = configure_logging(log_dir=tmp_path)
    try:
        assert log_file == tmp_path / LOG_FILE_NAME
        logging.getLogger("src.test").info("Bağlantı başlatıldı: ğüşiöç")
        logging.getLogger("httpx").info("HTTP Request: noisy per-query line")

        for handler in logging.getLogger().handlers:
            handler.flush()

        content = log_file.read_text(encoding="utf-8")
        assert "Bağlantı başlatıldı: ğüşiöç" in content
        assert "noisy per-query line" not in content
    finally:
        shutdown_logging()


def test_configure_logging_is_idempotent(tmp_path: Path) -> None:
    """Calling configure twice must not duplicate handlers (no doubled log lines)."""
    configure_logging(log_dir=tmp_path)
    configure_logging(log_dir=tmp_path)
    try:
        logging.getLogger("src.test").warning("tek satır")
        for handler in logging.getLogger().handlers:
            handler.flush()
        content = (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8")
        assert content.count("tek satır") == 1
    finally:
        shutdown_logging()


def test_read_log_tail_returns_last_lines_and_handles_missing(tmp_path: Path) -> None:
    """Tail reader returns only the most recent lines and never crashes on a missing file."""
    assert read_log_tail(tmp_path / "missing.log") == []

    log_file = tmp_path / "x.log"
    log_file.write_text("\n".join(f"satır {i}" for i in range(100)), encoding="utf-8")
    tail = read_log_tail(log_file, max_lines=5)
    assert tail == [f"satır {i}" for i in range(95, 100)]


def test_rate_limited_log_suppresses_floods_and_reports_skipped(caplog) -> None:
    """Repeated packet-path errors are logged once per interval with a skipped count."""
    now = [100.0]
    limiter = RateLimitedLog(
        logging.getLogger("src.flood"), interval_seconds=30.0, clock=lambda: now[0]
    )

    with caplog.at_level(logging.WARNING, logger="src.flood"):
        assert limiter.log(logging.WARNING, "reset", "Bağlantı sıfırlandı") is True
        for _ in range(9):
            assert limiter.log(logging.WARNING, "reset", "Bağlantı sıfırlandı") is False
        # A different key is independent
        assert limiter.log(logging.WARNING, "timeout", "Zaman aşımı") is True

        now[0] += 31.0
        assert limiter.log(logging.WARNING, "reset", "Bağlantı sıfırlandı") is True

    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 3
    assert "9 benzer kayıt atlandı" in messages[-1]
