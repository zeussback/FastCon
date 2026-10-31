"""Background WinDivert service for zero-latency DPI fragmentation and DoH DNS injection."""

from __future__ import annotations

import atexit
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pydivert

from src.config import AppSettings
from src.engine.doh_resolver import DoHResolver, extract_dns_question
from src.engine.packet_parser import (
    TCPFragment,
    build_http_fragments,
    build_tls_fragments,
    extract_http_host,
    extract_tls_sni,
)

WINDIVERT_FILTER = (
    "outbound and !loopback and ("
    "(tcp.DstPort == 443 and tcp.PayloadLength > 0) or "
    "(tcp.DstPort == 80 and tcp.PayloadLength > 0) or "
    "(udp.DstPort == 53 and udp.PayloadLength >= 12)"
    ")"
)

PacketSender = Callable[[pydivert.Packet], None]


@dataclass
class EngineStats:
    """Thread-safe runtime counters exposed to the FastCon desktop UI."""

    tls_fragmented: int = 0
    http_fragmented: int = 0
    dns_resolved: int = 0
    last_domain: str = "-"


class DPIService:
    """Captures only outbound handshake and DNS query packets via WinDivert."""

    def __init__(
        self,
        settings: AppSettings,
        doh_resolver: DoHResolver | None = None,
        divert_factory: Callable[[str], pydivert.WinDivert] = pydivert.WinDivert,
    ) -> None:
        self.settings = settings
        self.stats = EngineStats()
        self._doh = doh_resolver or DoHResolver(endpoints=settings.doh_endpoints)
        self._divert_factory = divert_factory
        self._handle: pydivert.WinDivert | None = None
        self._thread: threading.Thread | None = None
        self._dns_pool: ThreadPoolExecutor | None = None
        self._running = threading.Event()
        self._send_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._last_error: str | None = None
        atexit.register(self.stop)

    @property
    def is_running(self) -> bool:
        """Return True while the background WinDivert capture loop is active."""
        return self._running.is_set()

    @property
    def last_error(self) -> str | None:
        """Return the most recent startup/runtime error message, if any."""
        return self._last_error

    def update_settings(self, settings: AppSettings) -> None:
        """Update active profile, target domain list, and DoH endpoints on the fly."""
        self.settings = settings
        self._doh.endpoints = list(settings.doh_endpoints)

    def start(self) -> tuple[bool, str]:
        """Open WinDivert handle and launch background packet processing loop."""
        if self._running.is_set():
            return True, "FastCon motoru zaten çalışıyor."

        self._last_error = None
        try:
            handle = self._divert_factory(WINDIVERT_FILTER)
            handle.open()
        except PermissionError:
            self._last_error = "Yönetici (Administrator) izni gerekli."
            return False, self._last_error
        except OSError as exc:
            self._last_error = f"WinDivert sürücüsü başlatılamadı: {exc}"
            return False, self._last_error

        self._handle = handle
        self._dns_pool = ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="fastcon-doh"
        )
        self._running.set()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="fastcon-windivert",
            daemon=True,
        )
        self._thread.start()
        return True, "FastCon aktif — 0 ms ek gecikmeyle koruma çalışıyor."

    def stop(self) -> None:
        """Cleanly release WinDivert handle so Windows networking returns immediately to normal."""
        self._running.clear()
        handle = self._handle
        self._handle = None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

        thread = self._thread
        self._thread = None
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=2.0)

        pool = self._dns_pool
        self._dns_pool = None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    def _safe_send(self, packet: pydivert.Packet) -> None:
        """Inject a packet through the active WinDivert handle in a thread-safe manner."""
        with self._send_lock:
            handle = self._handle
            if handle is not None and self._running.is_set():
                try:
                    handle.send(packet)
                except OSError:
                    pass

    def _capture_loop(self) -> None:
        """Main packet capture loop executing on a background daemon thread."""
        while self._running.is_set():
            handle = self._handle
            if handle is None:
                break
            try:
                packet = handle.recv()
            except OSError:
                break
            if packet is None:
                continue
            self.process_packet(packet, send_fn=self._safe_send, async_dns=True)

    def process_packet(
        self,
        packet: pydivert.Packet,
        send_fn: PacketSender,
        *,
        async_dns: bool = False,
    ) -> None:
        """Inspect and fragment/resolve matching packets; pass everything else untouched."""
        try:
            if packet.udp is not None and packet.dst_port == 53:
                if async_dns and self._dns_pool is not None:
                    # Copy raw packet so async worker owns an independent buffer
                    cloned = pydivert.Packet(
                        bytes(packet.raw),
                        interface=packet.interface,
                        direction=packet.direction,
                    )
                    self._dns_pool.submit(self.handle_dns_packet_sync, cloned, send_fn)
                else:
                    self.handle_dns_packet_sync(packet, send_fn)
                return

            if packet.tcp is not None and packet.payload:
                if packet.dst_port == 443:
                    self._handle_tls_packet(packet, send_fn)
                    return
                if packet.dst_port == 80:
                    self._handle_http_packet(packet, send_fn)
                    return

            send_fn(packet)
        except (OSError, ValueError, IndexError, KeyError, RuntimeError):
            # Fail-safe guarantee: never drop a user's packet on unexpected parse errors
            send_fn(packet)

    def _handle_tls_packet(
        self, packet: pydivert.Packet, send_fn: PacketSender
    ) -> None:
        payload = bytes(packet.payload)
        sni_match = extract_tls_sni(payload)
        if sni_match is None or not self.settings.should_process_domain(
            sni_match.hostname
        ):
            send_fn(packet)
            return

        profile = self.settings.active_profile
        fragments = build_tls_fragments(payload, profile=profile, sni_match=sni_match)
        self._emit_tcp_fragments(packet, fragments, send_fn)

        with self._stats_lock:
            self.stats.tls_fragmented += 1
            self.stats.last_domain = sni_match.hostname

    def _handle_http_packet(
        self, packet: pydivert.Packet, send_fn: PacketSender
    ) -> None:
        payload = bytes(packet.payload)
        host = extract_http_host(payload)
        if host is None or not self.settings.should_process_domain(host):
            send_fn(packet)
            return

        profile = self.settings.active_profile
        fragments = build_http_fragments(payload, profile=profile)
        self._emit_tcp_fragments(packet, fragments, send_fn)

        with self._stats_lock:
            self.stats.http_fragmented += 1
            self.stats.last_domain = host

    def _emit_tcp_fragments(
        self,
        original: pydivert.Packet,
        fragments: list[TCPFragment],
        send_fn: PacketSender,
    ) -> None:
        if original.tcp is None or len(fragments) <= 1 and not fragments[0].is_fake:
            send_fn(original)
            return

        base_seq = original.tcp.seq_num
        base_raw = bytes(original.raw)

        for frag in fragments:
            seg = pydivert.Packet(
                base_raw,
                interface=original.interface,
                direction=original.direction,
            )
            assert seg.tcp is not None
            seg.tcp.seq_num = (base_seq + frag.seq_offset) & 0xFFFFFFFF
            if frag.is_fake and frag.ttl is not None:
                if seg.ipv4 is not None:
                    seg.ipv4.ttl = frag.ttl
                elif seg.ipv6 is not None:
                    seg.ipv6.hop_limit = frag.ttl
            seg.payload = frag.payload
            seg.recalculate_checksums()
            send_fn(seg)

    def handle_dns_packet_sync(
        self,
        packet: pydivert.Packet,
        send_fn: PacketSender,
    ) -> None:
        """Resolve matching outbound UDP/53 queries via DoH and inject INBOUND reply."""
        profile = self.settings.active_profile
        if not profile.enable_doh or not packet.payload:
            send_fn(packet)
            return

        query_bytes = bytes(packet.payload)
        question = extract_dns_question(query_bytes)
        if question is None or not self.settings.should_process_domain(question[0]):
            send_fn(packet)
            return

        doh_answer = self._doh.resolve_wire(query_bytes)
        if doh_answer is None:
            # Fail-safe fallback: forward original UDP DNS query if DoH endpoints are unreachable
            send_fn(packet)
            return

        reply = pydivert.Packet(
            bytes(packet.raw),
            interface=packet.interface,
            direction=pydivert.Direction.INBOUND,
        )
        reply.src_addr, reply.dst_addr = packet.dst_addr, packet.src_addr
        reply.src_port, reply.dst_port = packet.dst_port, packet.src_port
        reply.payload = doh_answer
        reply.recalculate_checksums()
        send_fn(reply)

        with self._stats_lock:
            self.stats.dns_resolved += 1
            self.stats.last_domain = question[0]
