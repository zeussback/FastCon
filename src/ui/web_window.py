"""Modern hardware-accelerated web interface controller via Microsoft Edge App Mode."""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Any

import eel

from src import __version__
from src.app_logging import get_log_dir, read_log_tail
from src.autostart import AutoStartManager
from src.config import (
    DEFAULT_TARGET_DOMAINS,
    DOH_PROVIDER_PRESETS,
    ISP_PROFILES,
    AppSettings,
    ScopeMode,
    normalize_domain,
)
from src.diagnostics.health_check import HealthChecker, ServiceHealth
from src.diagnostics.report import build_diagnostic_report
from src.engine.dpi_service import DPIService, _flush_windows_dns_cache
from src.ui.tray_icon import TrayController

logger = logging.getLogger("src.ui.web_window")

SW_RESTORE = 9
SW_SHOW = 5


def find_fastcon_window() -> int:
    """Find the HWND of the FastCon application window if currently open."""
    if sys.platform != "win32":
        return 0

    user32 = ctypes.windll.user32
    target_hwnd = 0

    def enum_proc(hwnd: int, _lparam: int) -> bool:
        nonlocal target_hwnd
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if title == "FastCon" or title.startswith("FastCon -"):
                    target_hwnd = hwnd
                    return False
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
    return target_hwnd


def restore_fastcon_window() -> bool:
    """Bring the existing FastCon window to the foreground if open."""
    if sys.platform != "win32":
        return False

    hwnd = find_fastcon_window()
    if hwnd != 0:
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        return True
    return False


class WebAppController:
    """Controller orchestrating FastCon core services, Eel RPC bridge, and System Tray."""

    def __init__(
        self,
        settings: AppSettings,
        dpi_service: DPIService,
        autostart_manager: AutoStartManager,
        health_checker: HealthChecker,
        is_admin: bool,
        on_request_elevate: Any = None,
        log_file: Path | None = None,
    ) -> None:
        self.settings = settings
        self.dpi_service = dpi_service
        self.autostart = autostart_manager
        self.health_checker = health_checker
        self.is_admin = is_admin
        self._on_request_elevate = on_request_elevate
        self._log_file = log_file

        self._last_health_results: list[ServiceHealth] = []
        self._tray_warned_once = False
        self._quitting = False
        self.tray: TrayController | None = None

    def setup_tray(self) -> None:
        """Initialize the background system tray icon with quick actions."""
        self.tray = TrayController(
            on_show=self.show_window,
            on_toggle=self.toggle_engine_from_tray,
            on_quit=self.quit_application,
            is_active_fn=lambda: self.dpi_service.is_running,
        )
        self.tray.start()

    def show_window(self) -> None:
        """Restore existing window or open a new Edge app mode window."""
        if not restore_fastcon_window():
            try:
                eel.show("index.html")
            except (OSError, RuntimeError) as exc:
                logger.error("Pencere açılırken hata oluştu: %s", exc)

    def toggle_engine_from_tray(self) -> None:
        """Toggle DPI service from system tray action."""
        if self.dpi_service.is_running:
            self.dpi_service.stop()
        else:
            self.dpi_service.start()
        if self.tray is not None:
            self.tray.refresh_state()

    def quit_application(self) -> None:
        """Cleanly terminate engine, tray icon, and the application process."""
        self._quitting = True
        logger.info("FastCon uygulaması kullanıcı isteğiyle kapatılıyor...")
        try:
            self.dpi_service.stop()
        except (OSError, RuntimeError) as exc:
            logger.warning("Motor durdurulurken hata: %s", exc)

        if self.tray is not None:
            self.tray.stop()

        # Exit cleanly
        os._exit(0)

    def handle_window_close(self, page: str, sockets: list[Any]) -> None:
        """Handle browser window close event dispatched by Eel."""
        logger.info(
            "Pencere soketi kapandı: page=%s, kalan_soketler=%d",
            page,
            len(sockets),
        )
        if len(sockets) == 0:
            if self.settings.minimize_to_tray:
                if not self._tray_warned_once and self.tray is not None:
                    self._tray_warned_once = True
                    self.tray.notify(
                        "FastCon arka planda çalışmaya devam ediyor.\n"
                        "Simgeye tıklayarak pencereyi açabilir veya sağ tıklayıp Çıkış yapabilirsiniz.",
                        title="FastCon Sistem Tepsisinde",
                    )
            else:
                self.quit_application()


def launch_web_app(
    settings: AppSettings,
    dpi_service: DPIService,
    autostart_manager: AutoStartManager,
    health_checker: HealthChecker,
    is_admin: bool,
    on_request_elevate: Any = None,
    start_minimized: bool = False,
    log_file: Path | None = None,
    instance_lock: Any = None,
) -> int:
    """Launch the modern Eel web application in Microsoft Edge App Mode."""
    controller = WebAppController(
        settings=settings,
        dpi_service=dpi_service,
        autostart_manager=autostart_manager,
        health_checker=health_checker,
        is_admin=is_admin,
        on_request_elevate=on_request_elevate,
        log_file=log_file,
    )

    # Sync autostart settings with actual Windows Registry status
    actual_autostart = autostart_manager.is_autostart_enabled()
    if actual_autostart != settings.autostart_enabled:
        settings.autostart_enabled = actual_autostart
        settings.save()

    controller.setup_tray()

    if instance_lock is not None:
        instance_lock.start_listener(controller.show_window)

    # Set up web assets directory
    web_dir = Path(__file__).resolve().parent / "web"
    eel.init(str(web_dir))

    # Register Eel RPC methods exposed to JavaScript
    @eel.expose
    def get_initial_state() -> dict[str, Any]:
        """Provide initial system configuration and status to frontend."""
        profiles_list = [
            {
                "id": pid,
                "display_name": prof.display_name,
                "description": prof.description,
            }
            for pid, prof in ISP_PROFILES.items()
        ]

        current_doh = ""
        current_endpoints = tuple(settings.doh_endpoints)
        for label, endpoints in DOH_PROVIDER_PRESETS.items():
            if current_endpoints == endpoints:
                current_doh = label
                break
        if not current_doh:
            current_doh = next(iter(DOH_PROVIDER_PRESETS))

        st = dpi_service.stats
        return {
            "version": __version__,
            "isAdmin": is_admin,
            "isRunning": dpi_service.is_running,
            "stats": {
                "tls_total": st.tls_fragmented + st.http_fragmented,
                "dns_resolved": st.dns_resolved,
                "last_domain": st.last_domain or "-",
            },
            "activeProfileId": settings.profile_id,
            "profiles": profiles_list,
            "scopeMode": settings.scope_mode.value,
            "targetDomains": list(settings.target_domains),
            "defaultDomains": list(DEFAULT_TARGET_DOMAINS),
            "dohProvider": current_doh,
            "dohProviders": list(DOH_PROVIDER_PRESETS.keys()),
            "minimizeToTray": settings.minimize_to_tray,
            "autostartEnabled": settings.autostart_enabled,
            "healthResults": [
                {
                    "service_name": r.service_name,
                    "reachable": r.reachable,
                    "is_partial": getattr(r, "is_partial", False),
                    "detail": r.detail,
                    "latency_ms": r.latency_ms,
                }
                for r in controller._last_health_results
            ],
        }

    @eel.expose
    def toggle_engine() -> dict[str, Any]:
        """Start or stop the background DPI & DoH engine."""
        if dpi_service.is_running:
            dpi_service.stop()
            msg = (
                "Bağlantı koruması durduruldu — ağ üzerinde hiçbir kalıntı bırakılmadı."
            )
        else:
            ok, msg = dpi_service.start()
            if not ok:
                return {"is_running": False, "message": msg}

        if controller.tray is not None:
            controller.tray.refresh_state()

        return {"is_running": dpi_service.is_running, "message": msg}

    @eel.expose
    def poll_engine_stats() -> dict[str, Any]:
        """Return real-time packet fragmentation and DNS query counters."""
        st = dpi_service.stats
        return {
            "tls_total": st.tls_fragmented + st.http_fragmented,
            "dns_resolved": st.dns_resolved,
            "last_domain": st.last_domain or "-",
        }

    @eel.expose
    def run_health_check() -> list[dict[str, Any]]:
        """Perform concurrent probes for Discord and Roblox."""
        results = health_checker.check_all()
        controller._last_health_results = list(results)
        return [
            {
                "service_name": r.service_name,
                "reachable": r.reachable,
                "is_partial": getattr(r, "is_partial", False),
                "detail": r.detail,
                "latency_ms": r.latency_ms,
            }
            for r in results
        ]

    @eel.expose
    def change_profile(profile_id: str) -> dict[str, Any]:
        """Update active connection profile and apply to running engine."""
        if profile_id not in ISP_PROFILES:
            profile_id = "standard"
        settings.profile_id = profile_id
        settings.save()
        dpi_service.update_settings(settings)
        prof = settings.active_profile
        return {
            "id": profile_id,
            "display_name": prof.display_name,
            "description": prof.description,
        }

    @eel.expose
    def change_scope(mode_str: str) -> None:
        """Switch scope between SELECTED_ONLY and ALL_SITES."""
        scope = (
            ScopeMode.ALL_SITES
            if mode_str == ScopeMode.ALL_SITES.value
            else ScopeMode.SELECTED_ONLY
        )
        settings.scope_mode = scope
        settings.save()
        dpi_service.update_settings(settings)

    @eel.expose
    def save_domains(domains: list[str]) -> list[str]:
        """Validate, normalize, and save target domains."""
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in domains:
            domain = normalize_domain(item)
            if domain and domain not in seen:
                seen.add(domain)
                cleaned.append(domain)
        if not cleaned:
            cleaned = list(DEFAULT_TARGET_DOMAINS)

        settings.target_domains = cleaned
        settings.save()
        dpi_service.update_settings(settings)
        return cleaned

    @eel.expose
    def change_doh(provider_label: str) -> None:
        """Update DoH resolver endpoint preset."""
        endpoints = DOH_PROVIDER_PRESETS.get(provider_label)
        if endpoints:
            settings.doh_endpoints = list(endpoints)
            settings.save()
            dpi_service.update_settings(settings)

    @eel.expose
    def flush_dns() -> None:
        """Clear DoH and Windows DNS cache."""
        dpi_service.clear_dns_cache()
        threading.Thread(target=_flush_windows_dns_cache, daemon=True).start()

    @eel.expose
    def toggle_tray_pref(enabled: bool) -> None:
        """Update minimize to tray preference."""
        settings.minimize_to_tray = bool(enabled)
        settings.save()

    @eel.expose
    def toggle_autostart(enabled: bool) -> dict[str, Any]:
        """Update Windows autostart configuration."""
        ok, msg = autostart_manager.set_autostart(enabled)
        if ok:
            settings.autostart_enabled = bool(enabled)
            settings.save()
        return {"enabled": settings.autostart_enabled, "message": msg}

    @eel.expose
    def get_diagnostic_report() -> str:
        """Build redacted diagnostic report string."""
        log_lines: list[str] = []
        if log_file is not None and log_file.exists():
            log_lines = read_log_tail(log_file, max_lines=40)
        return build_diagnostic_report(
            version=__version__,
            is_admin=is_admin,
            settings=settings,
            engine_running=dpi_service.is_running,
            stats=dpi_service.stats,
            last_error=dpi_service.last_error,
            health_results=controller._last_health_results,
            log_lines=log_lines,
        )

    @eel.expose
    def open_log_dir() -> None:
        """Open the application log directory in Windows Explorer."""
        log_dir = get_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(log_dir))  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            subprocess.run(["explorer", str(log_dir)], check=False)

    @eel.expose
    def trigger_elevation() -> None:
        """Relaunch as Administrator via UAC prompt."""
        if on_request_elevate is not None:
            on_request_elevate()

    # Determine browser engine mode (Prefer Microsoft Edge, fallback to Chrome)
    browser_mode = "edge"
    if not eel.edge.find_path():
        browser_mode = "chrome" if eel.chrome.find_path() else "default"

    cmd_args = [
        "--window-size=500,690",
        "--disable-features=Translate,OptimizationHints",
        "--no-first-run",
        "--disable-default-apps",
    ]

    if start_minimized:
        if is_admin:
            dpi_service.start()
            if controller.tray is not None:
                controller.tray.refresh_state()

        logger.info("FastCon simge durumunda başlatıldı (tepside aktif)")
        # Start server without opening initial browser window
        try:
            eel.start(
                "index.html",
                mode=False,
                host="127.0.0.1",
                port=0,
                block=True,
                cmdline_args=cmd_args,
                close_callback=controller.handle_window_close,
            )
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            controller.quit_application()
        return 0

    logger.info("FastCon Web UI (%s app modu) başlatılıyor...", browser_mode)
    try:
        eel.start(
            "index.html",
            mode=browser_mode,
            host="127.0.0.1",
            port=0,
            block=True,
            cmdline_args=cmd_args,
            close_callback=controller.handle_window_close,
        )
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        controller.quit_application()

    return 0
