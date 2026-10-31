"""FastCon application entry point with Windows UAC Administrator elevation."""

from __future__ import annotations

import argparse
import ctypes
import sys
from collections.abc import Sequence
from pathlib import Path

from src.autostart import AutoStartManager
from src.config import AppSettings
from src.diagnostics.health_check import HealthChecker
from src.engine.dpi_service import DPIService


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse FastCon command-line launch flags."""
    parser = argparse.ArgumentParser(
        prog="fastcon",
        description="FastCon — Zero-latency DPI bypass and DNS-over-HTTPS desktop app",
    )
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="Start minimized in the Windows System Tray and auto-enable protection.",
    )
    parser.add_argument(
        "--no-elevate",
        action="store_true",
        help="Do not automatically prompt for Windows UAC Administrator elevation.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def is_user_admin() -> bool:
    """Return True if the current process holds Windows Administrator privileges."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def relaunch_as_admin(extra_flags: Sequence[str] = ()) -> bool:
    """Trigger a Windows UAC elevation prompt to relaunch FastCon as Administrator."""
    project_root = Path(__file__).resolve().parent.parent
    python_exe = Path(sys.executable).resolve()
    pythonw = python_exe.with_name("pythonw.exe")
    target_exe = pythonw if pythonw.exists() else python_exe

    flag_str = " ".join(extra_flags)
    params = f"-m src.main {flag_str}".strip()
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            str(target_exe),
            params,
            str(project_root),
            1,
        )
        return int(rc) > 32
    except (AttributeError, OSError):
        return False


def main(argv: Sequence[str] | None = None) -> int:
    """Initialize configuration, elevate via UAC if needed, and launch the GUI."""
    args = parse_args(argv)
    admin = is_user_admin()

    if not admin and not args.no_elevate:
        forwarded = ["--minimized"] if args.minimized else []
        if relaunch_as_admin(forwarded):
            return 0

    # Lazy import GUI module after UAC check so elevation relaunch is instantaneous
    from src.ui.app_window import FastConApp

    settings = AppSettings.load()
    dpi_service = DPIService(settings=settings)
    autostart_manager = AutoStartManager()
    health_checker = HealthChecker()

    def handle_manual_elevate() -> None:
        if relaunch_as_admin():
            app.quit_application()

    app = FastConApp(
        settings=settings,
        dpi_service=dpi_service,
        autostart_manager=autostart_manager,
        health_checker=health_checker,
        is_admin=admin,
        on_request_elevate=handle_manual_elevate,
        start_minimized=args.minimized,
    )
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
