"""Monochrome dark CustomTkinter desktop interface with fluid animations and two-tab layout."""

from __future__ import annotations

import math
import os
import subprocess
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path

import customtkinter as ctk

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

# Monochrome Obsidian & Crisp White Design Tokens
BG_CANVAS = "#09090B"  # Zinc-950 deep obsidian background
BG_SURFACE = "#121215"  # Elevated card surface
BG_ELEVATED = "#18181D"  # Inner row / secondary pill surface
BG_HOVER = "#27272A"  # Subtle zinc interactive hover
BORDER_SUBTLE = "#222228"  # 1px structural hairline border
BORDER_FOCUS = "#3F3F46"  # Highlighted border

TEXT_WHITE = "#FAFAFA"  # Primary snow white text & active accents
TEXT_SECONDARY = "#A1A1AA"  # Neutral zinc-400 text
TEXT_MUTED = "#71717A"  # Muted zinc-500 captions

ACCENT_WHITE = "#FAFAFA"
ACCENT_WHITE_HOVER = "#E4E4E7"
ACCENT_DARK_TEXT = "#09090B"

STATUS_ONLINE = "#34D399"  # Mint emerald for active/reachable indicators
STATUS_WARNING = "#FBBF24"  # Warm amber for testing state
STATUS_OFFLINE = "#F87171"  # Soft coral for blocked/unreachable state

SCOPE_LABELS: dict[ScopeMode, str] = {
    ScopeMode.SELECTED_ONLY: "Sadece Seçili Uygulamalar",
    ScopeMode.ALL_SITES: "Tüm Siteler",
}
LABEL_TO_SCOPE: dict[str, ScopeMode] = {v: k for k, v in SCOPE_LABELS.items()}
SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def _blend_hex(hex_a: str, hex_b: str, t: float) -> str:
    """Linearly interpolate between two #RRGGBB colors for smooth canvas animations."""
    clamped = max(0.0, min(1.0, t))
    ra, ga, ba = int(hex_a[1:3], 16), int(hex_a[3:5], 16), int(hex_a[5:7], 16)
    rb, gb, bb = int(hex_b[1:3], 16), int(hex_b[3:5], 16), int(hex_b[5:7], 16)
    r = round(ra + (rb - ra) * clamped)
    g = round(ga + (gb - ga) * clamped)
    b = round(ba + (bb - ba) * clamped)
    return f"#{r:02x}{g:02x}{b:02x}"


class AnimatedPowerButton(tk.Canvas):
    """Custom 30-FPS vector canvas power orb with sonar pulse rings and hover physics."""

    def __init__(
        self,
        master: tk.Misc,
        on_click: Callable[[], None],
        width: int = 220,
        height: int = 168,
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            bg=BG_SURFACE,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._on_click = on_click
        self._canvas_w = width
        self._canvas_h = height
        self._active = False
        self._hovered = False
        self._pressed = False
        self._phase = 0.0
        self._hover_t = 0.0

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw_frame()

    def set_active(self, active: bool) -> None:
        """Update visual power state."""
        self._active = active
        self._draw_frame()

    def tick(self) -> None:
        """Advance animation phase by one 30-FPS frame (~33ms)."""
        self._phase = (self._phase + 0.045) % 1.0
        target_hover = 1.0 if self._hovered else 0.0
        self._hover_t += (target_hover - self._hover_t) * 0.28
        self._draw_frame()

    def _on_enter(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        self._hovered = True

    def _on_leave(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        self._hovered = False
        self._pressed = False

    def _on_press(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        self._pressed = True
        self._draw_frame()

    def _on_release(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        was_pressed = self._pressed
        self._pressed = False
        self._draw_frame()
        if was_pressed and self._hovered:
            self._on_click()

    def _draw_frame(self) -> None:
        self.delete("all")
        cx = self._canvas_w / 2
        cy = self._canvas_h / 2
        base_r = 52.0 + (self._hover_t * 3.0) - (3.0 if self._pressed else 0.0)

        if self._active:
            # Dual expanding sonar ripples fading smoothly into BG_SURFACE
            for offset in (0.0, 0.5):
                p = (self._phase + offset) % 1.0
                ripple_r = base_r + 4.0 + (p * 30.0)
                fade = (1.0 - p) ** 1.6
                ring_color = _blend_hex(BG_SURFACE, STATUS_ONLINE, fade * 0.65)
                self.create_oval(
                    cx - ripple_r,
                    cy - ripple_r,
                    cx + ripple_r,
                    cy + ripple_r,
                    outline=ring_color,
                    width=2,
                )

            # Outer glowing halo ring
            glow_t = 0.55 + 0.45 * math.sin(self._phase * math.tau)
            outer_border = _blend_hex("#1F2937", STATUS_ONLINE, glow_t)
            core_fill = _blend_hex("#141418", "#182622", self._hover_t)
            self.create_oval(
                cx - base_r,
                cy - base_r,
                cx + base_r,
                cy + base_r,
                fill=core_fill,
                outline=outer_border,
                width=2.5,
            )

            # Crisp white-emerald label & subtitle inside orb
            self.create_text(
                cx,
                cy - 8,
                text="DURDUR",
                fill=TEXT_WHITE,
                font=("Segoe UI", 14, "bold"),
            )
            self.create_text(
                cx,
                cy + 14,
                text="● AKTİF",
                fill=STATUS_ONLINE,
                font=("Segoe UI", 9, "bold"),
            )
        else:
            # Subtle breathing monochrome idle ring
            breath = 0.5 + 0.5 * math.sin(self._phase * math.tau)
            idle_ring_r = base_r + 6.0 + (breath * 3.5)
            idle_ring_color = _blend_hex(
                BG_SURFACE, "#2E2E36", 0.4 + 0.4 * self._hover_t
            )
            self.create_oval(
                cx - idle_ring_r,
                cy - idle_ring_r,
                cx + idle_ring_r,
                cy + idle_ring_r,
                outline=idle_ring_color,
                width=1.5,
            )

            # Crisp Snow White primary power orb with dark text
            orb_fill = _blend_hex("#F4F4F6", "#FFFFFF", self._hover_t)
            self.create_oval(
                cx - base_r,
                cy - base_r,
                cx + base_r,
                cy + base_r,
                fill=orb_fill,
                outline=TEXT_WHITE,
                width=2,
            )
            self.create_text(
                cx,
                cy - 7,
                text="BAŞLAT",
                fill=ACCENT_DARK_TEXT,
                font=("Segoe UI", 14, "bold"),
            )
            self.create_text(
                cx,
                cy + 14,
                text="HAZIR",
                fill="#52525B",
                font=("Segoe UI", 9, "bold"),
            )


class DomainEditorDialog(ctk.CTkToplevel):
    """Monochrome modal dialog for viewing and customizing target domains."""

    def __init__(
        self,
        parent: ctk.CTk,
        domains: list[str],
        on_save: Callable[[list[str]], None],
    ) -> None:
        super().__init__(parent)
        self.title("Hedef Alan Adları — FastCon")
        self.geometry("460x440")
        self.configure(fg_color=BG_CANVAS)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._on_save = on_save

        ctk.CTkLabel(
            self,
            text="Hedef Alan Adları",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color=TEXT_WHITE,
        ).pack(padx=22, pady=(20, 2), anchor="w")

        ctk.CTkLabel(
            self,
            text="Her satıra bir alan adı yazın. Alt alan adları otomatik kapsanır.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_SECONDARY,
        ).pack(padx=22, pady=(0, 12), anchor="w")

        self._textbox = ctk.CTkTextbox(
            self,
            height=255,
            fg_color=BG_SURFACE,
            border_color=BORDER_SUBTLE,
            border_width=1,
            text_color=TEXT_WHITE,
            font=ctk.CTkFont(family="Consolas", size=13),
        )
        self._textbox.pack(fill="both", expand=True, padx=22, pady=(0, 14))
        self._textbox.insert("1.0", "\n".join(domains))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=22, pady=(0, 20))

        ctk.CTkButton(
            btn_row,
            text="Varsayılana Dön",
            fg_color=BG_ELEVATED,
            hover_color=BG_HOVER,
            border_color=BORDER_SUBTLE,
            border_width=1,
            text_color=TEXT_WHITE,
            width=145,
            height=34,
            command=self._reset_defaults,
        ).pack(side="left")

        ctk.CTkButton(
            btn_row,
            text="Kaydet",
            fg_color=ACCENT_WHITE,
            hover_color=ACCENT_WHITE_HOVER,
            text_color=ACCENT_DARK_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            width=145,
            height=34,
            command=self._save_and_close,
        ).pack(side="right")

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
    """Monochrome dark desktop control center with Ana Ekran and Ayarlar tabs."""

    def __init__(
        self,
        settings: AppSettings,
        dpi_service: DPIService,
        autostart_manager: AutoStartManager,
        health_checker: HealthChecker,
        is_admin: bool,
        on_request_elevate: Callable[[], None] | None = None,
        start_minimized: bool = False,
        log_file: Path | None = None,
    ) -> None:
        ctk.set_appearance_mode("dark")
        super().__init__()

        self.settings = settings
        self.dpi_service = dpi_service
        self.autostart = autostart_manager
        self.health_checker = health_checker
        self.is_admin = is_admin
        self._on_request_elevate = on_request_elevate
        self._log_file = log_file
        self._last_health_results: list[ServiceHealth] = []
        self._tray_warned_once = False

        self._probe_in_flight = False
        self._spinner_idx = 0
        self._quitting = False
        self._current_tab = "home"

        self.title("FastCon")
        self.geometry("500x630")
        self.minsize(480, 610)
        self.configure(fg_color=BG_CANVAS)

        self._profile_names_to_id = {
            prof.display_name: pid for pid, prof in ISP_PROFILES.items()
        }

        self._build_header_and_tabs()
        self._build_home_tab()
        self._build_settings_tab()
        self.switch_tab("home")

        self.protocol("WM_DELETE_WINDOW", self._handle_window_close)

        self.tray = TrayController(
            on_show=lambda: self.after(0, self.show_window),
            on_toggle=lambda: self.after(0, self.toggle_engine),
            on_quit=lambda: self.after(0, self.quit_application),
            is_active_fn=lambda: self.dpi_service.is_running,
        )
        self.tray.start()

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

        self.after(33, self._animate_loop)
        self.after(750, self._poll_engine_stats)

    def _build_header_and_tabs(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(20, 12))

        brand_row = ctk.CTkFrame(header, fg_color="transparent")
        brand_row.pack(side="left")

        self._brand_dot = ctk.CTkLabel(
            brand_row,
            text="●",
            font=ctk.CTkFont(family="Segoe UI", size=14),
            text_color=TEXT_MUTED,
        )
        self._brand_dot.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(
            brand_row,
            text="FastCon",
            font=ctk.CTkFont(family="Segoe UI", size=21, weight="bold"),
            text_color=TEXT_WHITE,
        ).pack(side="left")

        # Sleek monochrome pill tab switcher
        tab_bar = ctk.CTkFrame(
            header,
            fg_color=BG_SURFACE,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        tab_bar.pack(side="right")

        self._tab_home_btn = ctk.CTkButton(
            tab_bar,
            text="Ana Ekran",
            width=96,
            height=30,
            corner_radius=7,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=lambda: self.switch_tab("home"),
        )
        self._tab_home_btn.pack(side="left", padx=(3, 2), pady=3)

        self._tab_settings_btn = ctk.CTkButton(
            tab_bar,
            text="Ayarlar",
            width=86,
            height=30,
            corner_radius=7,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=lambda: self.switch_tab("settings"),
        )
        self._tab_settings_btn.pack(side="left", padx=(2, 3), pady=3)

        # Container holding active tab frame
        self._content_area = ctk.CTkFrame(self, fg_color="transparent")
        self._content_area.pack(fill="both", expand=True, padx=24, pady=(0, 20))

    def _build_home_tab(self) -> None:
        self._home_frame = ctk.CTkFrame(self._content_area, fg_color="transparent")

        # 1. Animated Hero Power Card
        hero = ctk.CTkFrame(
            self._home_frame,
            fg_color=BG_SURFACE,
            corner_radius=16,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        hero.pack(fill="x", pady=(4, 10))

        if not self.is_admin:
            warn_bar = ctk.CTkFrame(hero, fg_color="#271F10", corner_radius=8)
            warn_bar.pack(fill="x", padx=16, pady=(12, 0))
            ctk.CTkLabel(
                warn_bar,
                text="Yönetici izni gerekli",
                font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                text_color=STATUS_WARNING,
            ).pack(side="left", padx=10, pady=4)
            ctk.CTkButton(
                warn_bar,
                text="Yükselt",
                width=68,
                height=22,
                fg_color=STATUS_WARNING,
                hover_color="#D97706",
                text_color=ACCENT_DARK_TEXT,
                font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                command=self._trigger_elevation,
            ).pack(side="right", padx=8, pady=4)

        self._power_canvas = AnimatedPowerButton(
            hero,
            on_click=self.toggle_engine,
            width=220,
            height=164,
        )
        self._power_canvas.pack(pady=(8, 0))

        self._status_title = ctk.CTkLabel(
            hero,
            text="Bağlantı Koruması Kapalı",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=TEXT_WHITE,
        )
        self._status_title.pack(pady=(0, 2))

        self._status_sub = ctk.CTkLabel(
            hero,
            text="Başlatmak için butona dokunun — oyun ve ses gecikmesi 0 ms.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_SECONDARY,
        )
        self._status_sub.pack(pady=(0, 6))

        self._stats_pill = ctk.CTkLabel(
            hero,
            text="TLS: 0   •   DoH DNS: 0   •   Son: -",
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color=TEXT_MUTED,
        )
        self._stats_pill.pack(pady=(0, 14))

        # 2. Live Access Status Card (Şimdi Test Et + Discord & Roblox)
        health_card = ctk.CTkFrame(
            self._home_frame,
            fg_color=BG_SURFACE,
            corner_radius=16,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        health_card.pack(fill="x", pady=8)

        health_top = ctk.CTkFrame(health_card, fg_color="transparent")
        health_top.pack(fill="x", padx=18, pady=(14, 10))

        ctk.CTkLabel(
            health_top,
            text="Erişim Durumu",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=TEXT_WHITE,
        ).pack(side="left")

        self._test_btn = ctk.CTkButton(
            health_top,
            text="Şimdi Test Et",
            width=126,
            height=30,
            corner_radius=8,
            fg_color=ACCENT_WHITE,
            hover_color=ACCENT_WHITE_HOVER,
            text_color=ACCENT_DARK_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self.run_health_check_async,
        )
        self._test_btn.pack(side="right")

        self._service_dots: dict[str, ctk.CTkLabel] = {}
        self._service_badges: dict[str, ctk.CTkLabel] = {}

        for svc_name in ("Discord", "Roblox"):
            row = ctk.CTkFrame(
                health_card,
                fg_color=BG_ELEVATED,
                corner_radius=10,
                border_width=1,
                border_color=BORDER_SUBTLE,
            )
            row.pack(fill="x", padx=18, pady=4)

            left_box = ctk.CTkFrame(row, fg_color="transparent")
            left_box.pack(side="left", padx=12, pady=9)

            dot = ctk.CTkLabel(
                left_box,
                text="●",
                font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color=TEXT_MUTED,
            )
            dot.pack(side="left", padx=(0, 8))
            self._service_dots[svc_name] = dot

            ctk.CTkLabel(
                left_box,
                text=svc_name,
                font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
                text_color=TEXT_WHITE,
            ).pack(side="left")

            badge = ctk.CTkLabel(
                row,
                text="Hazır",
                font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color=TEXT_SECONDARY,
            )
            badge.pack(side="right", padx=14, pady=9)
            self._service_badges[svc_name] = badge

        ctk.CTkFrame(health_card, height=10, fg_color="transparent").pack()

        # 3. Connection Profile Selector Card
        prof_card = ctk.CTkFrame(
            self._home_frame,
            fg_color=BG_SURFACE,
            corner_radius=16,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        prof_card.pack(fill="x", pady=(8, 0))

        prof_top = ctk.CTkFrame(prof_card, fg_color="transparent")
        prof_top.pack(fill="x", padx=18, pady=(14, 6))

        ctk.CTkLabel(
            prof_top,
            text="Bağlantı Profili",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=TEXT_WHITE,
        ).pack(side="left")

        profile_names = [prof.display_name for prof in ISP_PROFILES.values()]
        active_prof = self.settings.active_profile

        self._profile_menu = ctk.CTkOptionMenu(
            prof_card,
            values=profile_names,
            fg_color=BG_ELEVATED,
            button_color=BORDER_FOCUS,
            button_hover_color="#52525B",
            dropdown_fg_color=BG_SURFACE,
            dropdown_hover_color=BG_HOVER,
            text_color=TEXT_WHITE,
            height=34,
            corner_radius=8,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._on_profile_changed,
        )
        self._profile_menu.set(active_prof.display_name)
        self._profile_menu.pack(fill="x", padx=18, pady=(0, 6))

        self._profile_desc_lbl = ctk.CTkLabel(
            prof_card,
            text=active_prof.description,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_MUTED,
        )
        self._profile_desc_lbl.pack(anchor="w", padx=18, pady=(0, 14))

    def _build_settings_tab(self) -> None:
        self._settings_frame = ctk.CTkScrollableFrame(
            self._content_area,
            fg_color="transparent",
            scrollbar_button_color=BG_ELEVATED,
            scrollbar_button_hover_color=BORDER_FOCUS,
        )

        # 1. Scope & Target Domains Card
        scope_card = ctk.CTkFrame(
            self._settings_frame,
            fg_color=BG_SURFACE,
            corner_radius=16,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        scope_card.pack(fill="x", pady=(4, 10))

        ctk.CTkLabel(
            scope_card,
            text="Etki Kapsamı ve Alan Adları",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=TEXT_WHITE,
        ).pack(anchor="w", padx=18, pady=(16, 4))

        ctk.CTkLabel(
            scope_card,
            text="Korumayı yalnızca listedeki uygulamalara veya tüm trafiğe uygulayın.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_SECONDARY,
        ).pack(anchor="w", padx=18, pady=(0, 10))

        self._scope_seg = ctk.CTkSegmentedButton(
            scope_card,
            values=list(SCOPE_LABELS.values()),
            selected_color=ACCENT_WHITE,
            selected_hover_color=ACCENT_WHITE_HOVER,
            unselected_color=BG_ELEVATED,
            unselected_hover_color=BG_HOVER,
            text_color=ACCENT_DARK_TEXT,
            text_color_disabled=TEXT_MUTED,
            height=34,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self._on_scope_changed,
        )
        self._scope_seg.set(SCOPE_LABELS[self.settings.scope_mode])
        self._scope_seg.pack(fill="x", padx=18, pady=(0, 12))

        dom_row = ctk.CTkFrame(scope_card, fg_color=BG_ELEVATED, corner_radius=10)
        dom_row.pack(fill="x", padx=18, pady=(0, 16))

        self._domain_count_lbl = ctk.CTkLabel(
            dom_row,
            text=f"{len(self.settings.target_domains)} alan adı aktif",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_SECONDARY,
        )
        self._domain_count_lbl.pack(side="left", padx=14, pady=10)

        ctk.CTkButton(
            dom_row,
            text="Listeyi Düzenle",
            width=130,
            height=28,
            corner_radius=7,
            fg_color=ACCENT_WHITE,
            hover_color=ACCENT_WHITE_HOVER,
            text_color=ACCENT_DARK_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self._open_domain_editor,
        ).pack(side="right", padx=10, pady=10)

        # 2. Encrypted DNS (DoH) Card
        dns_card = ctk.CTkFrame(
            self._settings_frame,
            fg_color=BG_SURFACE,
            corner_radius=16,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        dns_card.pack(fill="x", pady=8)

        ctk.CTkLabel(
            dns_card,
            text="Şifreli DNS (DoH) Yapılandırması",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=TEXT_WHITE,
        ).pack(anchor="w", padx=18, pady=(16, 4))

        ctk.CTkLabel(
            dns_card,
            text="DNS sorgularının çözümleneceği şifreli HTTPS sunucusu.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_SECONDARY,
        ).pack(anchor="w", padx=18, pady=(0, 10))

        self._doh_menu = ctk.CTkOptionMenu(
            dns_card,
            values=list(DOH_PROVIDER_PRESETS.keys()),
            fg_color=BG_ELEVATED,
            button_color=BORDER_FOCUS,
            button_hover_color="#52525B",
            dropdown_fg_color=BG_SURFACE,
            dropdown_hover_color=BG_HOVER,
            text_color=TEXT_WHITE,
            height=34,
            corner_radius=8,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._on_doh_provider_changed,
        )
        self._doh_menu.set(self._current_doh_preset_label())
        self._doh_menu.pack(fill="x", padx=18, pady=(0, 10))

        flush_row = ctk.CTkFrame(dns_card, fg_color="transparent")
        flush_row.pack(fill="x", padx=18, pady=(0, 16))

        self._flush_feedback_lbl = ctk.CTkLabel(
            flush_row,
            text="Windows ve yerel DNS önbelleğini sıfırlar.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_MUTED,
        )
        self._flush_feedback_lbl.pack(side="left")

        self._flush_btn = ctk.CTkButton(
            flush_row,
            text="DNS Önbelleğini Temizle",
            width=155,
            height=28,
            corner_radius=7,
            fg_color=BG_ELEVATED,
            hover_color=BG_HOVER,
            border_color=BORDER_SUBTLE,
            border_width=1,
            text_color=TEXT_WHITE,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._on_flush_dns_clicked,
        )
        self._flush_btn.pack(side="right")

        # 3. System & Startup Preferences Card
        sys_card = ctk.CTkFrame(
            self._settings_frame,
            fg_color=BG_SURFACE,
            corner_radius=16,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        sys_card.pack(fill="x", pady=8)

        ctk.CTkLabel(
            sys_card,
            text="Sistem ve Başlangıç",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=TEXT_WHITE,
        ).pack(anchor="w", padx=18, pady=(16, 10))

        self._tray_switch = ctk.CTkSwitch(
            sys_card,
            text="Pencere kapatıldığında sistem tepsisine (Tray) küçült",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_WHITE,
            fg_color=BG_HOVER,
            progress_color=ACCENT_WHITE,
            button_color=TEXT_SECONDARY,
            button_hover_color=TEXT_WHITE,
            command=self._on_tray_pref_toggled,
        )
        if self.settings.minimize_to_tray:
            self._tray_switch.select()
        self._tray_switch.pack(anchor="w", padx=18, pady=(0, 10))

        self._autostart_switch = ctk.CTkSwitch(
            sys_card,
            text="Windows açılışında otomatik başlat (Tepside aktif)",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_WHITE,
            fg_color=BG_HOVER,
            progress_color=STATUS_ONLINE,
            button_color=TEXT_SECONDARY,
            button_hover_color=TEXT_WHITE,
            command=self._on_autostart_toggled,
        )
        self._autostart_switch.pack(anchor="w", padx=18, pady=(0, 10))

        self._settings_status_lbl = ctk.CTkLabel(
            sys_card,
            text=(
                "Durum: Yönetici yetkisi aktif"
                if self.is_admin
                else "Durum: Standart kullanıcı (Yönetici izni gerekli)"
            ),
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=STATUS_ONLINE if self.is_admin else STATUS_WARNING,
        )
        self._settings_status_lbl.pack(anchor="w", padx=18, pady=(0, 14))

        # 4. Diagnostics & Support Card
        diag_card = ctk.CTkFrame(
            self._settings_frame,
            fg_color=BG_SURFACE,
            corner_radius=16,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        diag_card.pack(fill="x", pady=8)

        ctk.CTkLabel(
            diag_card,
            text=f"Tanılama ve Destek (v{__version__})",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=TEXT_WHITE,
        ).pack(anchor="w", padx=18, pady=(16, 6))

        ctk.CTkLabel(
            diag_card,
            text="Erişim sorunu yaşarsanız tanı raporunu kopyalayıp GitHub Issue veya destek bildirimine ekleyebilirsiniz.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_SECONDARY,
            wraplength=410,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 12))

        diag_btns = ctk.CTkFrame(diag_card, fg_color="transparent")
        diag_btns.pack(fill="x", padx=18, pady=(0, 8))

        self._copy_report_btn = ctk.CTkButton(
            diag_btns,
            text="Tanı Raporunu Kopyala",
            width=175,
            height=30,
            corner_radius=7,
            fg_color=BG_ELEVATED,
            hover_color=BG_HOVER,
            border_color=BORDER_SUBTLE,
            border_width=1,
            text_color=TEXT_WHITE,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._on_copy_diagnostic_report,
        )
        self._copy_report_btn.pack(side="left", padx=(0, 8))

        self._open_log_btn = ctk.CTkButton(
            diag_btns,
            text="Log Klasörünü Aç",
            width=135,
            height=30,
            corner_radius=7,
            fg_color=BG_ELEVATED,
            hover_color=BG_HOVER,
            border_color=BORDER_SUBTLE,
            border_width=1,
            text_color=TEXT_SECONDARY,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            command=self._on_open_log_folder,
        )
        self._open_log_btn.pack(side="left")

        self._diag_feedback_lbl = ctk.CTkLabel(
            diag_card,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=STATUS_ONLINE,
        )
        self._diag_feedback_lbl.pack(anchor="w", padx=18, pady=(0, 12))

    def switch_tab(self, tab_name: str) -> None:
        """Switch between 'home' (Ana Ekran) and 'settings' (Ayarlar) views."""
        self._current_tab = tab_name
        if tab_name == "settings":
            self._home_frame.pack_forget()
            self._settings_frame.pack(fill="both", expand=True)
            self._tab_settings_btn.configure(
                fg_color=ACCENT_WHITE,
                hover_color=ACCENT_WHITE_HOVER,
                text_color=ACCENT_DARK_TEXT,
            )
            self._tab_home_btn.configure(
                fg_color="transparent",
                hover_color=BG_HOVER,
                text_color=TEXT_SECONDARY,
            )
        else:
            self._settings_frame.pack_forget()
            self._home_frame.pack(fill="both", expand=True)
            self._tab_home_btn.configure(
                fg_color=ACCENT_WHITE,
                hover_color=ACCENT_WHITE_HOVER,
                text_color=ACCENT_DARK_TEXT,
            )
            self._tab_settings_btn.configure(
                fg_color="transparent",
                hover_color=BG_HOVER,
                text_color=TEXT_SECONDARY,
            )

    def _animate_loop(self) -> None:
        """30-FPS UI animation tick driving the power orb and probe spinner."""
        if self._quitting:
            return
        if self._current_tab == "home":
            self._power_canvas.tick()
            if self._probe_in_flight:
                self._spinner_idx = (self._spinner_idx + 1) % len(SPINNER_FRAMES)
                spin = SPINNER_FRAMES[self._spinner_idx]
                self._test_btn.configure(text=f"{spin} Test Ediliyor")
        self.after(33, self._animate_loop)

    def _trigger_elevation(self) -> None:
        if self._on_request_elevate is not None:
            self._on_request_elevate()

    def toggle_engine(self) -> None:
        """Start or stop the background DPI & DoH service with animated state transition."""
        if self.dpi_service.is_running:
            self.dpi_service.stop()
            self._power_canvas.set_active(False)
            self._brand_dot.configure(text_color=TEXT_MUTED)
            self._status_title.configure(
                text="Bağlantı Koruması Kapalı", text_color=TEXT_WHITE
            )
            self._status_sub.configure(
                text="Durduruldu — ağ üzerinde hiçbir kalıntı bırakılmadı.",
                text_color=TEXT_SECONDARY,
            )
        else:
            ok, message = self.dpi_service.start()
            if ok:
                self._power_canvas.set_active(True)
                self._brand_dot.configure(text_color=STATUS_ONLINE)
                self._status_title.configure(
                    text="Bağlantı Koruması Aktif", text_color=STATUS_ONLINE
                )
                self._status_sub.configure(text=message, text_color=TEXT_SECONDARY)
                self.after(600, self.run_health_check_async)
            else:
                self._power_canvas.set_active(False)
                self._brand_dot.configure(text_color=STATUS_WARNING)
                self._status_title.configure(
                    text="Başlatılamadı", text_color=STATUS_WARNING
                )
                self._status_sub.configure(text=message, text_color=STATUS_WARNING)

        self.tray.refresh_state()

    def run_health_check_async(self) -> None:
        """Run concurrent Discord and Roblox probes with animated spinner feedback."""
        if self._probe_in_flight:
            return
        self._probe_in_flight = True
        self._test_btn.configure(state="disabled")
        for svc_name, badge in self._service_badges.items():
            badge.configure(text="Ölçülüyor...", text_color=STATUS_WARNING)
            self._service_dots[svc_name].configure(text_color=STATUS_WARNING)

        def worker() -> None:
            results = self.health_checker.check_all()
            if not self._quitting:
                self.after(0, lambda: self._apply_health_results(results))

        threading.Thread(target=worker, name="fastcon-probe", daemon=True).start()

    def _apply_health_results(self, results: list[ServiceHealth]) -> None:
        self._probe_in_flight = False
        self._last_health_results = list(results)
        self._test_btn.configure(state="normal", text="Şimdi Test Et")
        for item in results:
            badge = self._service_badges.get(item.service_name)
            dot = self._service_dots.get(item.service_name)
            if badge is None or dot is None:
                continue
            if item.reachable:
                dot.configure(text_color=STATUS_ONLINE)
                badge.configure(text=item.detail, text_color=TEXT_WHITE)
            else:
                dot.configure(text_color=STATUS_OFFLINE)
                badge.configure(text=item.detail, text_color=STATUS_OFFLINE)

    def _poll_engine_stats(self) -> None:
        if self._quitting:
            return
        st = self.dpi_service.stats
        total_tls = st.tls_fragmented + st.http_fragmented
        self._stats_pill.configure(
            text=f"TLS: {total_tls}   •   DoH DNS: {st.dns_resolved}   •   Son: {st.last_domain}"
        )
        self.after(750, self._poll_engine_stats)

    def _on_profile_changed(self, selected_label: str) -> None:
        profile_id = self._profile_names_to_id.get(selected_label, "standard")
        self.settings.profile_id = profile_id
        self.settings.save()
        self.dpi_service.update_settings(self.settings)
        prof = self.settings.active_profile
        self._profile_desc_lbl.configure(text=prof.description)

    def _on_scope_changed(self, selected_label: str) -> None:
        scope = LABEL_TO_SCOPE.get(selected_label, ScopeMode.SELECTED_ONLY)
        self.settings.scope_mode = scope
        self.settings.save()
        self.dpi_service.update_settings(self.settings)

    def _current_doh_preset_label(self) -> str:
        current = tuple(self.settings.doh_endpoints)
        for label, endpoints in DOH_PROVIDER_PRESETS.items():
            if current == endpoints:
                return label
        return next(iter(DOH_PROVIDER_PRESETS))

    def _on_doh_provider_changed(self, selected_label: str) -> None:
        endpoints = DOH_PROVIDER_PRESETS.get(selected_label)
        if endpoints:
            self.settings.doh_endpoints = list(endpoints)
            self.settings.save()
            self.dpi_service.update_settings(self.settings)

    def _on_flush_dns_clicked(self) -> None:
        self.dpi_service.clear_dns_cache()
        threading.Thread(target=_flush_windows_dns_cache, daemon=True).start()
        self._flush_feedback_lbl.configure(
            text="✓ DNS ve DoH önbelleği temizlendi.", text_color=STATUS_ONLINE
        )
        self.after(
            2500,
            lambda: self._flush_feedback_lbl.configure(
                text="Windows ve yerel DNS önbelleğini sıfırlar.",
                text_color=TEXT_MUTED,
            ),
        )

    def _on_copy_diagnostic_report(self) -> None:
        log_lines: list[str] = []
        if self._log_file is not None and self._log_file.exists():
            log_lines = read_log_tail(self._log_file, max_lines=40)
        report = build_diagnostic_report(
            version=__version__,
            is_admin=self.is_admin,
            settings=self.settings,
            engine_running=self.dpi_service.is_running,
            stats=self.dpi_service.stats,
            last_error=self.dpi_service.last_error,
            health_results=self._last_health_results,
            log_lines=log_lines,
        )
        self.clipboard_clear()
        self.clipboard_append(report)
        self._diag_feedback_lbl.configure(
            text="✓ Tanı raporu panoya kopyalandı!", text_color=STATUS_ONLINE
        )
        self.after(
            3500,
            lambda: (
                self._diag_feedback_lbl.configure(text="")
                if not self._quitting
                else None
            ),
        )

    def _on_open_log_folder(self) -> None:
        log_dir = get_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(log_dir))  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            subprocess.run(["explorer", str(log_dir)], check=False)

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
        self._domain_count_lbl.configure(text=f"{len(updated_domains)} alan adı aktif")

    def _on_tray_pref_toggled(self) -> None:
        self.settings.minimize_to_tray = bool(self._tray_switch.get())
        self.settings.save()

    def _on_autostart_toggled(self) -> None:
        desired = bool(self._autostart_switch.get())
        ok, message = self.autostart.set_autostart(desired)
        if ok:
            self.settings.autostart_enabled = desired
            self.settings.save()
            self._settings_status_lbl.configure(text=message, text_color=STATUS_ONLINE)
        else:
            if desired:
                self._autostart_switch.deselect()
            else:
                self._autostart_switch.select()
            self._settings_status_lbl.configure(text=message, text_color=STATUS_WARNING)

    def show_window(self) -> None:
        """Restore the FastCon window from the Windows System Tray."""
        self.deiconify()
        self.lift()
        self.focus_force()

    def _handle_window_close(self) -> None:
        """Minimize to tray if configured, otherwise shut down cleanly."""
        if self.settings.minimize_to_tray:
            if not self._tray_warned_once:
                self._tray_warned_once = True
                self.tray.notify(
                    "FastCon arka planda çalışmaya devam ediyor.\n"
                    "Simgeye tıklayarak pencereyi açabilir veya sağ tıklayıp Çıkış yapabilirsiniz.",
                    title="FastCon Sistem Tepsisinde",
                )
            self.withdraw()
        else:
            self.quit_application()

    def quit_application(self) -> None:
        """Stop WinDivert service, remove tray icon, and terminate GUI loop."""
        self._quitting = True
        self.dpi_service.stop()
        self.tray.stop()
        self.destroy()
