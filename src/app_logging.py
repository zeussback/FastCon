"""Persistent rotating log file so user-reported problems can actually be diagnosed."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

APP_NAME = "FastCon"
LOG_FILE_NAME = "fastcon.log"
_LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"
_MAX_BYTES = 512 * 1024
_BACKUP_COUNT = 3

# Third-party loggers that emit one INFO line per HTTP/DoH request
_NOISY_LOGGERS = ("httpx", "httpcore", "hpack", "PIL")

_installed_handler: logging.Handler | None = None


def get_app_data_dir() -> Path:
    """Return %LOCALAPPDATA%\\FastCon (user-writable, survives re-downloading the app)."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / APP_NAME


def get_log_dir() -> Path:
    """Return the directory holding FastCon's rotating log files."""
    return get_app_data_dir() / "logs"


def configure_logging(log_dir: Path | None = None, level: int = logging.INFO) -> Path:
    """Attach a UTF-8 rotating file handler to the root logger (idempotent)."""
    global _installed_handler

    target_dir = log_dir or get_log_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    log_file = target_dir / LOG_FILE_NAME

    shutdown_logging()

    handler = RotatingFileHandler(
        log_file, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))

    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    _installed_handler = handler
    _install_exception_hooks()
    return log_file


def shutdown_logging() -> None:
    """Detach and close the FastCon file handler (used on reconfigure and in tests)."""
    global _installed_handler
    if _installed_handler is not None:
        logging.getLogger().removeHandler(_installed_handler)
        _installed_handler.close()
        _installed_handler = None


def _install_exception_hooks() -> None:
    """Route uncaught exceptions (main + worker threads) into the log file."""
    crash_logger = logging.getLogger("fastcon.crash")

    def handle_main(
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        crash_logger.critical("Yakalanmamış hata", exc_info=(exc_type, exc, tb))

    def handle_thread(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit or args.exc_value is None:
            return
        thread_name = args.thread.name if args.thread else "?"
        crash_logger.critical(
            "İş parçacığında yakalanmamış hata (%s)",
            thread_name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = handle_main
    threading.excepthook = handle_thread


def read_log_tail(log_file: Path, max_lines: int = 80) -> list[str]:
    """Return the last ``max_lines`` lines of a log file, or [] if unavailable."""
    try:
        with log_file.open("r", encoding="utf-8", errors="replace") as fh:
            return [line.rstrip("\r\n") for line in deque(fh, maxlen=max_lines)]
    except OSError:
        return []


class RateLimitedLog:
    """Log a given key at most once per interval so hot paths cannot flood the file."""

    def __init__(
        self,
        target: logging.Logger,
        interval_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._logger = target
        self._interval = interval_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._last_emit: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}

    def log(
        self,
        level: int,
        key: str,
        msg: str,
        *args: object,
        exc_info: bool = False,
    ) -> bool:
        """Emit the record unless ``key`` was logged within the interval. Returns True if emitted."""
        now = self._clock()
        with self._lock:
            last = self._last_emit.get(key)
            if last is not None and now - last < self._interval:
                self._suppressed[key] = self._suppressed.get(key, 0) + 1
                return False
            if len(self._last_emit) > 512:
                self._last_emit.clear()
                self._suppressed.clear()
            self._last_emit[key] = now
            skipped = self._suppressed.pop(key, 0)

        if skipped:
            msg = f"{msg} (son {int(self._interval)} sn içinde {skipped} benzer kayıt atlandı)"
        self._logger.log(level, msg, *args, exc_info=exc_info)
        return True
