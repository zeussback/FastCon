"""Modern dark-themed CustomTkinter desktop window for FastCon."""

from __future__ import annotations

import threading
from collections.abc import Callable

import customtkinter as ctk

from src.autostart import AutoStartManager
from src.config import (
    DEFAULT_TARGET_DOMAINS,
    ISP_PROFILES,
    AppSettings,
    ScopeMode,
    normalize_domain,
)
from src.diagnostics.health_check import HealthChecker, ServiceHealth
from src.engine.dpi_service import DPIService
from src.ui.tray_icon import TrayController

# Palette constants — Slate dark surface with Emerald active and Rose alert accents
BG_ROOT = "#0B1120"
BG_CARD = "#131C31"
BORDER_SUBTLE = "#1E293B"
TEXT_PRIMARY = "#F8FAFC"
TEXT_MUTED = "#94A3B8"
ACCENT_EMERALD = "#10B981"
ACCENT_EMERALD_HOVER = "#059669"
ACCENT_ROSE = "#EF4444"
ACCENT_ROSE_HOVER = "#DC2626"
ACCENT_BLUE = "#3B82F6"
ACCENT_BLUE_HOVER = "#2563EB"
ACCENT_AMBER = "#F59E0B"

SCOPE_LABELS: dict[ScopeMode, str] = {
    ScopeMode.SELECTED_ONLY: "Yalnızca Seçili Siteler (Discord & Roblox)",
    ScopeMode.ALL_SITES: "Tüm Engelli Siteler",
}
LABEL_TO_SCOPE: dict[str, ScopeMode] = {v: k for k, v in SCOPE_LABELS.items()}


class DomainEditorDialog(ctk.CTkToplevel):
    """Modal dialog allowing the user to inspect and edit the target domain list."""

    def __init__(
        self,
        parent: ctk.CTk,
        domains: list[str],
        on_save: Callable[[list[str]], None],
    ) -> None:
        super().__init__(parent)
        self.title("Hedef Alan Adları — FastCon")
        self.geometry("460x440")
        self.configure(fg_color=BG_ROOT)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._on_save = on_save

        header = ctk.CTkLabel(
            self,
            text="Hedef Alan Adları (Her satıra bir domain)",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=TEXT_PRIMARY,
        )
        header.pack(padx=20, pady=(18, 4), anchor="w")

        hint = ctk.CTkLabel(
            self,
            text="Alt alan adları (ör. gateway.discord.gg, tr.rbxcdn.com) otomatik kapsanır.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_MUTED,
        )
        hint.pack(padx=20, pady=(0, 10), anchor="w")

        self._textbox = ctk.CTkTextbox(
            self,
            height=260,
            fg_color=BG_CARD,
            border_color=BORDER_SUBTLE,
            border_width=1,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Consolas", size=13),
        )
        self._textbox.pack(fill="both", expand=True, padx=20, pady=(0, 14))
        self._textbox.insert("1.0", "\n".join(domains))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(0, 18))

        reset_btn = ctk.CTkButton(
            btn_row,
            text="Varsayılana Sıfırla",
            fg_color=BORDER_SUBTLE,
            hover_color="#334155",
            text_color=TEXT_PRIMARY,
            width=150,
            command=self._reset_defaults,
        )
        reset_btn.pack(side="left")

        save_btn = ctk.CTkButton(
            btn_row,
            text="Kaydet ve Uygula",
            fg_color=ACCENT_BLUE,
            hover_color=ACCENT_BLUE_HOVER,
            width=150,
            command=self._save_and_close,
        )
        save_btn.pack(side="right")

    def _reset_defaults(self) -> None:
        self._textbox.delete("1.0", "end")
        self._textbox.insert("1.0", "\n".join(DEFAULT_TARGET_DOMAINS))

    def _save_and_close(self) -> None:
        raw_lines = self._textbox.get("1.0", "end").splitlines()
        cleaned: list[str] = []
        seen: set[str] = set()
        for line in raw_lines:
            domain = normalize_domain(line)
            if domain and domain not in seen:
                seen.add(domain)
                cleaned.append(domain)
        if not cleaned:
            cleaned = list(DEFAULT_TARGET_DOMAINS)
        self._on_save(cleaned)
        self.destroy()


class FastConApp(ctk.CTk):
    """Main FastCon desktop control center with System Tray integration."""

    def __init__(
        self,
        settings: AppSettings,
        dpi_service: DPIService,
        autostart_manager: AutoStartManager,
        health_checker: HealthChecker,
        is_admin: bool,
        on_request_elevate: Callable[[], None] | None = None,
        start_minimized: bool = False,
    ) -> None:
        ctk.set_appearance_mode("dark")
        super().__init__()

        self.settings = settings
        self.dpi_service = dpi_service
        self.autostart = autostart_manager
        self.health_checker = health_checker
        self.is_admin = is_admin
        self._on_request_elevate = on_request_elevate
        self._probe_in_flight = False
        self._quitting = False

        self.title("FastCon — Sıfır Gecikmeli DPI & DoH Bağlantı Aracı")
        self.geometry("560x690")
        self.minsize(540, 660)
        self.configure(fg_color=BG_ROOT)

        self._profile_names_to_id = {
            prof.display_name: pid for pid, prof in ISP_PROFILES.items()
        }

        self._build_layout()
        self.protocol("WM_DELETE_WINDOW", self._handle_window_close)

        self.tray = TrayController(
            on_show=lambda: self.after(0, self.show_window),
            on_toggle=lambda: self.after(0, self.toggle_engine),
            on_quit=lambda: self.after(0, self.quit_application),
            is_active_fn=lambda: self.dpi_service.is_running,
        )
        self.tray.start()

        # Sync actual Windows Task Scheduler state into UI switch on launch
        actual_autostart = self.autostart.is_autostart_enabled()
        if actual_autostart != self.settings.autostart_enabled:
            self.settings.autostart_enabled = actual_autostart
            self.settings.save()
        if actual_autostart:
            self._autostart_switch.select()
        else:
            self._autostart_switch.deselect()

        if start_minimized:
            self.withdraw()
            if self.is_admin:
                self.after(200, self.toggle_engine)
        else:
            self.after(400, self.run_health_check_async)

        self.after(750, self._poll_engine_stats)

    def _build_layout(self) -> None:
        # 1. Top Header Bar
        top_bar = ctk.CTkFrame(self, fg_color="transparent")
        top_bar.pack(fill="x", padx=24, pady=(20, 10))

        title_col = ctk.CTkFrame(top_bar, fg_color="transparent")
        title_col.pack(side="left")

        brand = ctk.CTkLabel(
            title_col,
            text="⚡ FastCon",
            font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold"),
            text_color=TEXT_PRIMARY,
        )
        brand.pack(anchor="w")

        subtitle = ctk.CTkLabel(
            title_col,
            text="Superonline & Türkiye Sıfır Ping DPI / DoH Motoru",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_MUTED,
        )
        subtitle.pack(anchor="w")

        if self.is_admin:
            admin_badge = ctk.CTkLabel(
                top_bar,
                text="● Yönetici Yetkisi Aktif",
                font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
                text_color=ACCENT_EMERALD,
            )
            admin_badge.pack(side="right", pady=6)
        else:
            elevate_btn = ctk.CTkButton(
                top_bar,
                text="⚠ Yönetici Olarak Başlat",
                fg_color=ACCENT_AMBER,
                hover_color="#D97706",
                text_color="#0F172A",
                font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
                height=32,
                command=self._trigger_elevation,
            )
            elevate_btn.pack(side="right", pady=6)

        # 2. Hero Engine Power Card
        hero = ctk.CTkFrame(
            self,
            fg_color=BG_CARD,
            corner_radius=14,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        hero.pack(fill="x", padx=24, pady=8)

        self._status_pill = ctk.CTkLabel(
            hero,
            text="○ KORUMA KAPALI",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=TEXT_MUTED,
        )
        self._status_pill.pack(pady=(16, 6))

        self._power_btn = ctk.CTkButton(
            hero,
            text="BAŞLAT",
            height=52,
            width=240,
            corner_radius=12,
            fg_color=ACCENT_EMERALD,
            hover_color=ACCENT_EMERALD_HOVER,
            font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold"),
            command=self.toggle_engine,
        )
        self._power_btn.pack(pady=6)

        self._status_hint = ctk.CTkLabel(
            hero,
            text="Oyun içi UDP ve ses paketlerine dokunulmaz — 0 ms ping etkisi.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_MUTED,
        )
        self._status_hint.pack(pady=(4, 6))

        self._stats_label = ctk.CTkLabel(
            hero,
            text="TLS (SNI): 0  •  Şifreli DoH DNS: 0  •  Son Alan Adı: -",
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color="#64748B",
        )
        self._stats_label.pack(pady=(0, 14))

        # 3. Live Connectivity Health Check Card
        health_card = ctk.CTkFrame(
            self,
            fg_color=BG_CARD,
            corner_radius=14,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        health_card.pack(fill="x", padx=24, pady=8)

        health_header = ctk.CTkFrame(health_card, fg_color="transparent")
        health_header.pack(fill="x", padx=18, pady=(14, 8))

        ctk.CTkLabel(
            health_header,
            text="Canlı Erişim Durumu",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="left")

        self._test_btn = ctk.CTkButton(
            health_header,
            text="Şimdi Test Et",
            width=115,
            height=28,
            fg_color=BORDER_SUBTLE,
            hover_color="#334155",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self.run_health_check_async,
        )
        self._test_btn.pack(side="right")

        self._service_badges: dict[str, ctk.CTkLabel] = {}
        for svc_name in ("Discord", "Roblox"):
            row = ctk.CTkFrame(health_card, fg_color="#0F172A", corner_radius=8)
            row.pack(fill="x", padx=18, pady=4)
            ctk.CTkLabel(
                row,
                text=svc_name,
                font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
                text_color=TEXT_PRIMARY,
            ).pack(side="left", padx=14, pady=8)

            badge = ctk.CTkLabel(
                row,
                text="Kontrol bekleniyor...",
                font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color=TEXT_MUTED,
            )
            badge.pack(side="right", padx=14, pady=8)
            self._service_badges[svc_name] = badge

        ctk.CTkFrame(health_card, height=8, fg_color="transparent").pack()

        # 4. ISP Profile & Domain Scope Card
        cfg_card = ctk.CTkFrame(
            self,
            fg_color=BG_CARD,
            corner_radius=14,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        cfg_card.pack(fill="x", padx=24, pady=8)

        ctk.CTkLabel(
            cfg_card,
            text="Bağlantı Profili ve Kapsam",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w", padx=18, pady=(14, 8))

        profile_names = [prof.display_name for prof in ISP_PROFILES.values()]
        current_prof_name = self.settings.active_profile.display_name

        self._profile_menu = ctk.CTkOptionMenu(
            cfg_card,
            values=profile_names,
            fg_color="#1E293B",
            button_color=ACCENT_BLUE,
            button_hover_color=ACCENT_BLUE_HOVER,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._on_profile_changed,
        )
        self._profile_menu.set(current_prof_name)
        self._profile_menu.pack(fill="x", padx=18, pady=(0, 10))

        self._scope_seg = ctk.CTkSegmentedButton(
            cfg_card,
            values=list(SCOPE_LABELS.values()),
            selected_color=ACCENT_BLUE,
            selected_hover_color=ACCENT_BLUE_HOVER,
            unselected_color="#0F172A",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._on_scope_changed,
        )
        self._scope_seg.set(SCOPE_LABELS[self.settings.scope_mode])
        self._scope_seg.pack(fill="x", padx=18, pady=(0, 10))

        domain_row = ctk.CTkFrame(cfg_card, fg_color="transparent")
        domain_row.pack(fill="x", padx=18, pady=(0, 14))

        self._domain_count_lbl = ctk.CTkLabel(
            domain_row,
            text=f"Hedef listede {len(self.settings.target_domains)} alan adı tanımlı",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_MUTED,
        )
        self._domain_count_lbl.pack(side="left")

        ctk.CTkButton(
            domain_row,
            text="Alan Adlarını Düzenle",
            width=155,
            height=28,
            fg_color=BORDER_SUBTLE,
            hover_color="#334155",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._open_domain_editor,
        ).pack(side="right")

        # 5. System Preferences Footer Card (Tray + Optional Autostart)
        sys_card = ctk.CTkFrame(
            self,
            fg_color=BG_CARD,
            corner_radius=14,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        sys_card.pack(fill="x", padx=24, pady=(8, 18))

        self._tray_switch = ctk.CTkSwitch(
            sys_card,
            text="Pencere kapatıldığında sistem tepsisine (Tray) küçült",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_PRIMARY,
            progress_color=ACCENT_BLUE,
            command=self._on_tray_pref_toggled,
        )
        if self.settings.minimize_to_tray:
            self._tray_switch.select()
        self._tray_switch.pack(anchor="w", padx=18, pady=(14, 8))

        self._autostart_switch = ctk.CTkSwitch(
            sys_card,
            text="Windows açılışında otomatik başlat (Sistem tepsisinde aktif)",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_PRIMARY,
            progress_color=ACCENT_EMERALD,
            command=self._on_autostart_toggled,
        )
        self._autostart_switch.pack(anchor="w", padx=18, pady=(0, 14))

    def _trigger_elevation(self) -> None:
        if self._on_request_elevate is not None:
            self._on_request_elevate()

    def toggle_engine(self) -> None:
        """Start or stop the WinDivert DPI + DoH background engine."""
        if self.dpi_service.is_running:
            self.dpi_service.stop()
            self._status_pill.configure(text="○ KORUMA KAPALI", text_color=TEXT_MUTED)
            self._power_btn.configure(
                text="BAŞLAT",
                fg_color=ACCENT_EMERALD,
                hover_color=ACCENT_EMERALD_HOVER,
            )
            self._status_hint.configure(
                text="Durduruldu — ağ üzerinde hiçbir kalıcı değişiklik bırakılmadı.",
                text_color=TEXT_MUTED,
            )
        else:
            ok, message = self.dpi_service.start()
            if ok:
                self._status_pill.configure(
                    text="● KORUMA AKTİF — 0 ms Ek Gecikme",
                    text_color=ACCENT_EMERALD,
                )
                self._power_btn.configure(
                    text="DURDUR",
                    fg_color=ACCENT_ROSE,
                    hover_color=ACCENT_ROSE_HOVER,
                )
                self._status_hint.configure(text=message, text_color=TEXT_PRIMARY)
                self.after(900, self.run_health_check_async)
            else:
                self._status_pill.configure(
                    text="⚠ BAŞLATILAMADI", text_color=ACCENT_AMBER
                )
                self._status_hint.configure(text=message, text_color=ACCENT_AMBER)

        self.tray.refresh_state()

    def run_health_check_async(self) -> None:
        """Run concurrent Discord and Roblox HTTPS probes without blocking the GUI."""
        if self._probe_in_flight:
            return
        self._probe_in_flight = True
        self._test_btn.configure(state="disabled", text="Test Ediliyor...")
        for badge in self._service_badges.values():
            badge.configure(
                text="Bağlantı kontrol ediliyor...", text_color=ACCENT_AMBER
            )

        def worker() -> None:
            results = self.health_checker.check_all()
            if not self._quitting:
                self.after(0, lambda: self._apply_health_results(results))

        threading.Thread(target=worker, name="fastcon-probe", daemon=True).start()

    def _apply_health_results(self, results: list[ServiceHealth]) -> None:
        self._probe_in_flight = False
        self._test_btn.configure(state="normal", text="Şimdi Test Et")
        for item in results:
            badge = self._service_badges.get(item.service_name)
            if badge is None:
                continue
            if item.reachable:
                badge.configure(text=f"● {item.detail}", text_color=ACCENT_EMERALD)
            else:
                badge.configure(text=f"● {item.detail}", text_color=ACCENT_ROSE)

    def _poll_engine_stats(self) -> None:
        if self._quitting:
            return
        st = self.dpi_service.stats
        total_tls = st.tls_fragmented + st.http_fragmented
        self._stats_label.configure(
            text=(
                f"TLS/HTTP Parçalanan: {total_tls}  •  "
                f"Şifreli DoH DNS: {st.dns_resolved}  •  "
                f"Son Alan Adı: {st.last_domain}"
            )
        )
        self.after(750, self._poll_engine_stats)

    def _on_profile_changed(self, selected_label: str) -> None:
        profile_id = self._profile_names_to_id.get(selected_label, "superonline")
        self.settings.profile_id = profile_id
        self.settings.save()
        self.dpi_service.update_settings(self.settings)
        self._status_hint.configure(
            text=f"Profil güncellendi: {self.settings.active_profile.display_name}",
            text_color=TEXT_MUTED,
        )

    def _on_scope_changed(self, selected_label: str) -> None:
        scope = LABEL_TO_SCOPE.get(selected_label, ScopeMode.SELECTED_ONLY)
        self.settings.scope_mode = scope
        self.settings.save()
        self.dpi_service.update_settings(self.settings)

    def _open_domain_editor(self) -> None:
        DomainEditorDialog(
            parent=self,
            domains=self.settings.target_domains,
            on_save=self._on_domains_saved,
        )

    def _on_domains_saved(self, updated_domains: list[str]) -> None:
        self.settings.target_domains = updated_domains
        self.settings.save()
        self.dpi_service.update_settings(self.settings)
        self._domain_count_lbl.configure(
            text=f"Hedef listede {len(updated_domains)} alan adı tanımlı"
        )

    def _on_tray_pref_toggled(self) -> None:
        self.settings.minimize_to_tray = bool(self._tray_switch.get())
        self.settings.save()

    def _on_autostart_toggled(self) -> None:
        desired = bool(self._autostart_switch.get())
        ok, message = self.autostart.set_autostart(desired)
        if ok:
            self.settings.autostart_enabled = desired
            self.settings.save()
            self._status_hint.configure(text=message, text_color=TEXT_PRIMARY)
        else:
            # Revert switch if Windows denied task creation (e.g., not running as Admin)
            if desired:
                self._autostart_switch.deselect()
            else:
                self._autostart_switch.select()
            self._status_hint.configure(text=message, text_color=ACCENT_AMBER)

    def show_window(self) -> None:
        """Restore the FastCon window from the Windows System Tray."""
        self.deiconify()
        self.lift()
        self.focus_force()

    def _handle_window_close(self) -> None:
        """Minimize to tray if configured, otherwise shut down cleanly."""
        if self.settings.minimize_to_tray:
            self.withdraw()
        else:
            self.quit_application()

    def quit_application(self) -> None:
        """Stop WinDivert service, remove tray icon, and terminate GUI loop."""
        self._quitting = True
        self.dpi_service.stop()
        self.tray.stop()
        self.destroy()
