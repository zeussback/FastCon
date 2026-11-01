"""Tests for the copy-to-clipboard diagnostic report used in bug reports."""

from __future__ import annotations

from src.config import AppSettings, ScopeMode
from src.diagnostics.health_check import ServiceHealth
from src.diagnostics.report import build_diagnostic_report
from src.engine.dpi_service import EngineStats


def test_report_contains_environment_settings_health_and_log_tail() -> None:
    """Report bundles everything a maintainer needs without requiring screenshots."""
    settings = AppSettings(profile_id="deep", scope_mode=ScopeMode.ALL_SITES)
    stats = EngineStats(tls_fragmented=12, http_fragmented=1, dns_resolved=7)
    health = [
        ServiceHealth("Discord", "https://discord.com", True, 56, "56 ms"),
        ServiceHealth("Roblox", "https://www.roblox.com", False, None, "Zaman Aşımı"),
    ]

    report = build_diagnostic_report(
        version="0.2.0",
        is_admin=True,
        settings=settings,
        engine_running=True,
        stats=stats,
        last_error="WinDivert sürücüsü başlatılamadı",
        health_results=health,
        log_lines=["2026-11-01 INFO motor başladı"],
        platform_text="Windows-11-10.0.26100",
        python_text="3.11.9",
    )

    assert "FastCon 0.2.0" in report
    assert "Windows-11-10.0.26100" in report
    assert "Yönetici: Evet" in report
    assert "deep" in report
    assert "all_sites" in report
    assert "TLS: 13" in report and "DoH DNS: 7" in report
    assert "Discord: ERİŞİLEBİLİR (56 ms)" in report
    assert "Roblox: ERİŞİLEMİYOR (Zaman Aşımı)" in report
    assert "WinDivert sürücüsü başlatılamadı" in report
    assert "motor başladı" in report


def test_report_includes_endpoint_breakdown_and_partial_blocks() -> None:
    """Report displays individual sub-endpoints and marks partial blocks."""
    from src.diagnostics.health_check import EndpointHealth

    health = [
        ServiceHealth(
            service_name="Discord",
            url="https://discord.com",
            reachable=False,
            latency_ms=53,
            detail="Kısmi: Web Açık, Ses/Gateway Engelli",
            endpoints=(
                EndpointHealth(
                    "Web & API",
                    "https://discord.com/cdn-cgi/trace",
                    True,
                    53,
                    "Erişilebilir (53 ms)",
                ),
                EndpointHealth(
                    "Ses & Gateway",
                    "https://gateway.discord.gg/",
                    False,
                    None,
                    "DPI Engeli / Zaman Aşımı",
                ),
            ),
            is_partial=True,
        )
    ]
    report = build_diagnostic_report(
        version="0.2.0",
        is_admin=True,
        settings=AppSettings(),
        engine_running=True,
        stats=EngineStats(),
        last_error=None,
        health_results=health,
        log_lines=[],
    )
    assert "Discord: KISMİ ENGEL (Kısmi: Web Açık, Ses/Gateway Engelli)" in report
    assert "[✓] Web & API: Erişilebilir (53 ms)" in report
    assert "[✗] Ses & Gateway: DPI Engeli / Zaman Aşımı" in report


def test_report_handles_missing_health_and_logs() -> None:
    """Report is still well-formed before the first health check or log write."""
    report = build_diagnostic_report(
        version="0.2.0",
        is_admin=False,
        settings=AppSettings(),
        engine_running=False,
        stats=EngineStats(),
        last_error=None,
        health_results=[],
        log_lines=[],
        platform_text="Windows-10",
        python_text="3.11.9",
    )
    assert "Yönetici: Hayır" in report
    assert "Henüz test yapılmadı" in report
    assert "Log kaydı yok" in report
    # Personal data must not leak: no username/home path in the report
    assert "C:\\Users\\" not in report
