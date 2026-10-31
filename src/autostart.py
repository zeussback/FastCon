"""User-controlled Windows Startup manager using Task Scheduler (ONLOGON)."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

TASK_NAME = "FastConAutoStart"
CommandRunner = Callable[[list[str]], tuple[int, str]]


def _default_command_runner(command: list[str]) -> tuple[int, str]:
    """Execute a system command without flashing a console window."""
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            creationflags=creation_flags,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        return completed.returncode, output.strip()
    except OSError as exc:
        return 1, str(exc)


def _resolve_gui_python_executable() -> Path:
    """Prefer pythonw.exe next to sys.executable so boot launch has no console window."""
    current = Path(sys.executable).resolve()
    pythonw = current.with_name("pythonw.exe")
    if pythonw.exists():
        return pythonw
    return current


class AutoStartManager:
    """Manages optional Windows logon startup for FastCon with highest privileges."""

    def __init__(
        self,
        task_name: str = TASK_NAME,
        project_root: Path | None = None,
        command_runner: CommandRunner = _default_command_runner,
    ) -> None:
        self.task_name = task_name
        self.project_root = (
            project_root or Path(__file__).resolve().parent.parent
        ).resolve()
        self._run = command_runner

    def is_autostart_enabled(self) -> bool:
        """Return True if the FastCon logon scheduled task is currently registered."""
        code, _ = self._run(["schtasks", "/Query", "/TN", self.task_name])
        return code == 0

    def set_autostart(self, enabled: bool) -> tuple[bool, str]:
        """Create or remove the Windows logon scheduled task based on user choice."""
        if enabled:
            python_exe = _resolve_gui_python_executable()
            task_command = (
                f'cmd.exe /c "cd /d "{self.project_root}" && '
                f'"{python_exe}" -m src.main --minimized"'
            )
            cmd = [
                "schtasks",
                "/Create",
                "/TN",
                self.task_name,
                "/TR",
                task_command,
                "/SC",
                "ONLOGON",
                "/RL",
                "HIGHEST",
                "/F",
            ]
            code, output = self._run(cmd)
            if code == 0:
                return True, "Windows açılışında otomatik başlatma etkinleştirildi."
            return (
                False,
                f"Otomatik başlatma kaydedilemedi (Yönetici izni gerekli): {output}",
            )

        cmd = ["schtasks", "/Delete", "/TN", self.task_name, "/F"]
        code, output = self._run(cmd)
        if code == 0:
            return True, "Windows açılışında otomatik başlatma devre dışı bırakıldı."
        # If task was already absent, check query so disabling an already-off setting is idempotent
        if not self.is_autostart_enabled():
            return True, "Otomatik başlatma zaten kapalı."
        return False, f"Otomatik başlatma kaldırılamadı: {output}"
