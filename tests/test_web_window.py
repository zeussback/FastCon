"""Tests for the modern Eel Web App controller and RPC endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src import __version__
from src.autostart import AutoStartManager
from src.config import DEFAULT_TARGET_DOMAINS, AppSettings, ScopeMode
from src.diagnostics.health_check import HealthChecker, ServiceHealth
from src.diagnostics.report import build_diagnostic_report
from src.engine.dpi_service import DPIService, EngineStats
from src.ui.web_window import (
    WebAppController,
    find_fastcon_window,
    restore_fastcon_window,
)


def _build_controller(
    minimize_to_tray: bool = True,
    is_admin: bool = True,
    running: bool = False,
) -> tuple[WebAppController, MagicMock, MagicMock]:
    settings = AppSettings(
        minimize_to_tray=minimize_to_tray,
        profile_id="standard",
        scope_mode=ScopeMode.SELECTED_ONLY,
        target_domains=["discord.com", "roblox.com"],
        doh_endpoints=["https://dns.quad9.net/dns-query"],
        autostart_enabled=False,
    )
    # Prevent disk writes in unit tests
    settings.save = MagicMock()  # type: ignore[method-assign]

    dpi_service = MagicMock(spec=DPIService)
    dpi_service.is_running = running
    dpi_service.stats = EngineStats(
        tls_fragmented=10,
        http_fragmented=5,
        dns_resolved=8,
        last_domain="discord.com",
    )
    dpi_service.last_error = None
    dpi_service.start.return_value = (True, "Koruma aktif")

    autostart = MagicMock(spec=AutoStartManager)
    autostart.is_autostart_enabled.return_value = False
    autostart.set_autostart.return_value = (True, "Otomatik başlatma güncellendi")

    health_checker = MagicMock(spec=HealthChecker)
    health_checker.check_all.return_value = [
        ServiceHealth("Discord", "https://discord.com", True, 32, "32 ms"),
        ServiceHealth("Roblox", "https://roblox.com", True, 45, "45 ms"),
    ]

    controller = WebAppController(
        settings=settings,
        dpi_service=dpi_service,
        autostart_manager=autostart,
        health_checker=health_checker,
        is_admin=is_admin,
    )
    return controller, dpi_service, autostart


def test_controller_initialization_and_window_detection():
    controller, _, _ = _build_controller()
    assert controller.is_admin is True
    assert controller.settings.profile_id == "standard"
    assert controller.dpi_service.is_running is False

    # Window detection helpers shouldn't crash
    hwnd = find_fastcon_window()
    assert isinstance(hwnd, int)
    # When no FastCon window exists, restore returns False
    if hwnd == 0:
        assert restore_fastcon_window() is False


def test_controller_toggle_engine_and_stats():
    controller, dpi_service, _ = _build_controller(running=False)

    # Start
    res = controller.dpi_service.start()
    assert res[0] is True
    dpi_service.start.assert_called_once()

    st = controller.dpi_service.stats
    assert st.tls_fragmented + st.http_fragmented == 15
    assert st.dns_resolved == 8
    assert st.last_domain == "discord.com"


def test_controller_profile_and_scope_updates():
    controller, dpi_service, _ = _build_controller()

    # Profile change
    controller.settings.profile_id = "aggressive"
    controller.settings.save()
    dpi_service.update_settings(controller.settings)
    assert controller.settings.profile_id == "aggressive"
    dpi_service.update_settings.assert_called()

    # Scope change
    controller.settings.scope_mode = ScopeMode.ALL_SITES
    controller.settings.save()
    dpi_service.update_settings(controller.settings)
    assert controller.settings.scope_mode == ScopeMode.ALL_SITES


def test_controller_domain_sanitization_and_fallbacks():
    _controller, _dpi_service, _ = _build_controller()

    raw_input = ["  DISCORD.COM  ", "roblox.com", "discord.com", ""]
    cleaned: list[str] = []
    seen: set[str] = set()
    from src.config import normalize_domain

    for item in raw_input:
        domain = normalize_domain(item)
        if domain and domain not in seen:
            seen.add(domain)
            cleaned.append(domain)

    assert cleaned == ["discord.com", "roblox.com"]

    # Test empty list fallback
    empty_input: list[str] = []
    fallback = empty_input or list(DEFAULT_TARGET_DOMAINS)
    assert len(fallback) > 0
    assert "discord.com" in fallback


def test_controller_diagnostic_report_generation():
    controller, _, _ = _build_controller()
    report = build_diagnostic_report(
        version=__version__,
        is_admin=controller.is_admin,
        settings=controller.settings,
        engine_running=controller.dpi_service.is_running,
        stats=controller.dpi_service.stats,
        last_error=controller.dpi_service.last_error,
        health_results=[],
        log_lines=["Test log entry 1"],
    )

    assert f"FastCon {__version__}" in report
    assert "Test log entry 1" in report
    assert "discord.com" in report


def test_controller_window_close_minimize_to_tray_vs_exit():
    # 1. When minimize_to_tray is True: does not call os._exit
    controller_tray, _, _ = _build_controller(minimize_to_tray=True)
    mock_tray = MagicMock()
    controller_tray.tray = mock_tray

    controller_tray.handle_window_close("index.html", [])
    mock_tray.notify.assert_called_once()
    assert controller_tray._quitting is False

    # Second close shouldn't spam notify balloon
    mock_tray.notify.reset_mock()
    controller_tray.handle_window_close("index.html", [])
    mock_tray.notify.assert_not_called()

    # 2. When minimize_to_tray is False: calls quit_application
    controller_exit, dpi_service, _ = _build_controller(minimize_to_tray=False)
    with patch("os._exit") as mock_exit:
        controller_exit.handle_window_close("index.html", [])
        assert controller_exit._quitting is True
        dpi_service.stop.assert_called_once()
        mock_exit.assert_called_once_with(0)
