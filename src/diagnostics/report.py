"""Plain-text diagnostic report that users can paste into a GitHub issue."""

from __future__ import annotations

import os
import platform
from collections.abc import Sequence

from src.config import AppSettings
from src.diagnostics.health_check import ServiceHealth
from src.engine.dpi_service import EngineStats


def _redact(text: str) -> str:
    """Strip the user's home directory / username from paths in log lines."""
    home = os.path.expanduser("~")
    user = os.environ.get("USERNAME") or os.environ.get("USER")
    if home and home != "~":
        text = text.replace(home, "%USERPROFILE%")
    if user and len(user) > 2:
        text = text.replace(f"\\{user}\\", "\\<kullanıcı>\\")
    return text


def build_diagnostic_report(
    *,
    version: str,
    is_admin: bool,
    settings: AppSettings,
    engine_running: bool,
    stats: EngineStats,
    last_error: str | None,
    health_results: Sequence[ServiceHealth],
    log_lines: Sequence[str],
    platform_text: str | None = None,
    python_text: str | None = None,
) -> str:
    """Assemble environment, settings, engine state, health and recent logs."""
    lines: list[str] = [
        "=== FastCon Tanı Raporu ===",
        f"FastCon {version}",
        f"Sistem: {platform_text or platform.platform()}",
        f"Python: {python_text or platform.python_version()}",
        f"Yönetici: {'Evet' if is_admin else 'Hayır'}",
        "",
        "--- Ayarlar ---",
        f"Profil: {settings.profile_id}",
        f"Kapsam: {settings.scope_mode.value}",
        f"Hedef alan adı sayısı: {len(settings.target_domains)}",
        f"DoH: {', '.join(settings.doh_endpoints)}",
        f"Tepsiye küçült: {settings.minimize_to_tray}",
        f"Otomatik başlat: {settings.autostart_enabled}",
        "",
        "--- Motor ---",
        f"Durum: {'Çalışıyor' if engine_running else 'Durduruldu'}",
        (
            f"TLS: {stats.tls_fragmented + stats.http_fragmented}  "
            f"DoH DNS: {stats.dns_resolved}  Son alan adı: {stats.last_domain}"
        ),
        f"Son hata: {last_error or '-'}",
        "",
        "--- Erişim Testi ---",
    ]

    if health_results:
        for item in health_results:
            state = "ERİŞİLEBİLİR" if item.reachable else "ERİŞİLEMİYOR"
            lines.append(f"{item.service_name}: {state} ({item.detail})")
    else:
        lines.append("Henüz test yapılmadı")

    lines += ["", "--- Son Log Kayıtları ---"]
    if log_lines:
        lines += [_redact(line) for line in log_lines]
    else:
        lines.append("Log kaydı yok")

    return "\n".join(lines)
