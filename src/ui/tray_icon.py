"""Windows System Tray (Notification Area) icon and quick action menu."""

from __future__ import annotations

import threading
from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw


def create_status_icon_image(active: bool, size: int = 64) -> Image.Image:
    """Render an anti-aliased obsidian & white/emerald lightning tray icon."""
    scale = 2
    canvas_size = size * scale
    img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Obsidian dark base with crisp white/emerald border ring
    bg_fill = (18, 18, 21, 255)
    border_fill = (52, 211, 153, 255) if active else (113, 113, 122, 255)
    bolt_fill = (250, 250, 250, 255) if active else (161, 161, 170, 255)

    margin = 8
    draw.rounded_rectangle(
        (margin, margin, canvas_size - margin, canvas_size - margin),
        radius=30,
        fill=bg_fill,
        outline=border_fill,
        width=6,
    )

    bolt = [
        (72, 22),
        (40, 68),
        (62, 68),
        (54, 106),
        (88, 58),
        (66, 58),
    ]
    draw.polygon(bolt, fill=bolt_fill)
    return img.resize((size, size), Image.Resampling.LANCZOS)


class TrayController:
    """Manages the background pystray icon and dispatches clicks to the GUI thread."""

    def __init__(
        self,
        on_show: Callable[[], None],
        on_toggle: Callable[[], None],
        on_quit: Callable[[], None],
        is_active_fn: Callable[[], bool],
    ) -> None:
        self._on_show = on_show
        self._on_toggle = on_toggle
        self._on_quit = on_quit
        self._is_active_fn = is_active_fn
        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the system tray icon on a daemon thread if not already running."""
        if self._icon is not None:
            return

        menu = pystray.Menu(
            pystray.MenuItem(
                "FastCon Penceresini Aç",
                lambda _icon, _item: self._on_show(),
                default=True,
            ),
            pystray.MenuItem(
                lambda _item: (
                    "Korumayı Durdur" if self._is_active_fn() else "Korumayı Başlat"
                ),
                lambda _icon, _item: self._on_toggle(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Çıkış (Tamamen Kapat)",
                lambda _icon, _item: self._on_quit(),
            ),
        )

        active = self._is_active_fn()
        self._icon = pystray.Icon(
            name="FastCon",
            icon=create_status_icon_image(active=active),
            title="FastCon — Aktif" if active else "FastCon — Hazır",
            menu=menu,
        )
        self._thread = threading.Thread(
            target=self._icon.run,
            name="fastcon-tray",
            daemon=True,
        )
        self._thread.start()

    def refresh_state(self) -> None:
        """Update the tray icon graphic and tooltip text when engine state changes."""
        if self._icon is None:
            return
        active = self._is_active_fn()
        self._icon.icon = create_status_icon_image(active=active)
        self._icon.title = (
            "FastCon — Koruma Aktif (0 ms Ek Ping)" if active else "FastCon — Kapalı"
        )
        try:
            self._icon.update_menu()
        except (OSError, RuntimeError):
            pass

    def stop(self) -> None:
        """Remove the tray icon cleanly upon application exit."""
        icon = self._icon
        self._icon = None
        if icon is not None:
            try:
                icon.stop()
            except (OSError, RuntimeError):
                pass
