"""Windows named-mutex single-instance guard with a "bring window to front" signal.

Two FastCon processes would open two WinDivert handles and two TLS proxies on the same
traffic, so a second launch must hand control to the already-running instance instead.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Callable
from ctypes import wintypes

logger = logging.getLogger(__name__)

DEFAULT_INSTANCE_NAME = "FastCon"

_ERROR_ALREADY_EXISTS = 183
_ERROR_ACCESS_DENIED = 5
_WAIT_OBJECT_0 = 0x00000000
_EVENT_MODIFY_STATE = 0x0002
_SDDL_REVISION_1 = 1
_POLL_MS = 400

# SYSTEM, Administrators and Authenticated Users get full access; the Medium
# mandatory label lets a non-elevated relaunch signal an elevated instance.
_SHARED_SDDL = "D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;AU)S:(ML;;NW;;;ME)"


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]


def _mutex_name(name: str) -> str:
    return f"Local\\{name}.SingleInstance.Mutex"


def _event_name(name: str) -> str:
    return f"Local\\{name}.SingleInstance.Activate"


def _load_kernel32() -> ctypes.WinDLL | None:
    if sys.platform != "win32":
        return None
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateEventW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    k32.CreateEventW.restype = wintypes.HANDLE
    k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k32.OpenEventW.restype = wintypes.HANDLE
    k32.SetEvent.argtypes = [wintypes.HANDLE]
    k32.SetEvent.restype = wintypes.BOOL
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.LocalFree.argtypes = [wintypes.HLOCAL]
    k32.LocalFree.restype = wintypes.HLOCAL
    return k32


class _SharedSecurity:
    """Owns a SECURITY_ATTRIBUTES built from ``_SHARED_SDDL`` (None if unavailable)."""

    def __init__(self, k32: ctypes.WinDLL) -> None:
        self._k32 = k32
        self._psd = wintypes.LPVOID()
        self.attributes: _SecurityAttributes | None = None
        try:
            adv = ctypes.WinDLL("advapi32", use_last_error=True)
            fn = adv.ConvertStringSecurityDescriptorToSecurityDescriptorW
            fn.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.LPVOID),
                ctypes.POINTER(wintypes.ULONG),
            ]
            fn.restype = wintypes.BOOL
            if fn(_SHARED_SDDL, _SDDL_REVISION_1, ctypes.byref(self._psd), None):
                self.attributes = _SecurityAttributes(
                    ctypes.sizeof(_SecurityAttributes), self._psd, False
                )
        except (OSError, AttributeError):
            logger.debug(
                "Paylaşımlı güvenlik tanımlayıcısı oluşturulamadı", exc_info=True
            )

    def pointer(self) -> ctypes._Pointer | None:  # type: ignore[type-arg]
        return ctypes.pointer(self.attributes) if self.attributes else None

    def free(self) -> None:
        if self._psd:
            self._k32.LocalFree(self._psd)
            self._psd = wintypes.LPVOID()
            self.attributes = None


class SingleInstanceLock:
    """Process-wide lock: ``acquire()`` returns False if FastCon is already running."""

    def __init__(self, name: str = DEFAULT_INSTANCE_NAME) -> None:
        self.name = name
        self._k32 = _load_kernel32()
        self._mutex: int | None = None
        self._event: int | None = None
        self._stop = threading.Event()
        self._listener: threading.Thread | None = None

    @property
    def listener_alive(self) -> bool:
        """True while the activation-signal listener thread is running."""
        return self._listener is not None and self._listener.is_alive()

    def acquire(self) -> bool:
        """Create the named mutex; False means another instance already owns it."""
        if self._k32 is None:
            return True
        if self._mutex is not None:
            return True

        security = _SharedSecurity(self._k32)
        try:
            handle = self._k32.CreateMutexW(
                security.pointer(), False, _mutex_name(self.name)
            )
            err = ctypes.get_last_error()
            if not handle:
                if err == _ERROR_ACCESS_DENIED:
                    # Mutex exists but belongs to an elevated instance we cannot open
                    return False
                logger.warning("Tek örnek kilidi oluşturulamadı (hata %s)", err)
                return True  # Fail open: never block the app on an unexpected API error
            if err == _ERROR_ALREADY_EXISTS:
                self._k32.CloseHandle(handle)
                return False

            self._mutex = handle
            event = self._k32.CreateEventW(
                security.pointer(), False, False, _event_name(self.name)
            )
            self._event = event or None
            return True
        finally:
            security.free()

    def start_listener(self, on_activate: Callable[[], None]) -> None:
        """Invoke ``on_activate`` (from a worker thread) whenever a second launch signals."""
        if self._k32 is None or self._event is None or self.listener_alive:
            return
        self._stop.clear()
        k32, event = self._k32, self._event

        def run() -> None:
            while not self._stop.is_set():
                if k32.WaitForSingleObject(event, _POLL_MS) == _WAIT_OBJECT_0:
                    if self._stop.is_set():
                        break
                    logger.info("İkinci başlatma algılandı, pencere öne getiriliyor")
                    try:
                        on_activate()
                    except Exception:
                        logger.exception("Pencere öne getirilemedi")

        self._listener = threading.Thread(
            target=run, name="fastcon-instance-listener", daemon=True
        )
        self._listener.start()

    def release(self) -> None:
        """Stop the listener and close kernel handles (safe to call repeatedly)."""
        self._stop.set()
        listener = self._listener
        self._listener = None
        if listener is not None and listener is not threading.current_thread():
            listener.join(timeout=2.0)

        if self._k32 is None:
            return
        for attr in ("_event", "_mutex"):
            handle = getattr(self, attr)
            if handle:
                self._k32.CloseHandle(handle)
            setattr(self, attr, None)


def signal_existing_instance(name: str = DEFAULT_INSTANCE_NAME) -> bool:
    """Ask the running FastCon instance to show its window. Returns False on failure."""
    k32 = _load_kernel32()
    if k32 is None:
        return False
    handle = k32.OpenEventW(_EVENT_MODIFY_STATE, False, _event_name(name))
    if not handle:
        return False
    try:
        return bool(k32.SetEvent(handle))
    finally:
        k32.CloseHandle(handle)


def show_already_running_message() -> None:
    """Fallback notice when the running instance could not be signalled."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            "FastCon zaten çalışıyor.\n\n"
            "Saatin yanındaki sistem tepsisinde (Tray) FastCon simgesine tıklayarak "
            "pencereyi açabilirsiniz.",
            "FastCon",
            0x40,  # MB_ICONINFORMATION
        )
    except (OSError, AttributeError):
        pass
