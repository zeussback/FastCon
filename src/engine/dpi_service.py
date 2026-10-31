"""Background WinDivert service for zero-latency DPI bypass and DoH DNS injection."""

from __future__ import annotations

import atexit
import socket
import subprocess
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import dns.exception
import dns.message
import dns.rdatatype
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
from src.engine.tls_proxy import BTK_SINKHOLE_PREFIX, TLSRecordProxy

PacketSender = Callable[[pydivert.Packet], None]

# Standard and Cloudflare/Discord Voice TLS signaling ports
_TLS_PORTS = frozenset({443, 2053, 2083, 2087, 2096, 8443})


def build_windivert_filter(proxy_port: int) -> str:
    """Build surgical outbound WinDivert filter excluding UDP voice/gaming and DoH servers."""
    tls_port_expr = " or ".join(f"tcp.DstPort == {p}" for p in sorted(_TLS_PORTS))
    proxy_clause = f" or tcp.SrcPort == {proxy_port}" if proxy_port > 0 else ""
    return (
        "outbound and !loopback and ("
        "(udp.DstPort == 53 and udp.PayloadLength >= 12) or "
        "("
        "ip and "
        "ip.DstAddr != 1.1.1.1 and ip.DstAddr != 1.0.0.1 and "
        "ip.DstAddr != 8.8.8.8 and ip.DstAddr != 8.8.4.4 and ("
        f"({tls_port_expr}){proxy_clause} or "
        "(tcp.DstPort == 80 and tcp.PayloadLength > 0)"
        ")"
        ")"
        ")"
    )


def _host_has_global_ipv6() -> bool:
    """Return True if the machine currently has a non-link-local global IPv6 address."""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET6)
        for info in infos:
            addr = str(info[4][0]).lower()
            if not addr.startswith(("fe80:", "::1")):
                return True
    except OSError:
        return False
    return False


def _flush_windows_dns_cache() -> None:
    """Purge stale/poisoned OS DNS cache entries (e.g. 195.175.254.2) upon engine start."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(
            ["ipconfig", "/flushdns"],
            capture_output=True,
            check=False,
            timeout=4,
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        pass


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
        self._tls_proxy = TLSRecordProxy(
            settings_getter=lambda: self.settings,
            doh_resolver=self._doh,
            on_tls_bypassed=self._record_tls_bypass,
        )

        self._handle: pydivert.WinDivert | None = None
        self._thread: threading.Thread | None = None
        self._dns_pool: ThreadPoolExecutor | None = None
        self._running = threading.Event()
        self._send_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._target_ips: set[str] = set()
        self._has_global_ipv6 = False
        self._last_error: str | None = None
        atexit.register(self.stop)

    @property
    def is_running(self) -> bool:
        """Return True while the background WinDivert capture loop is active."""
        return self._running.is_set()

    @property
    def proxy_port(self) -> int:
        """Return the local transparent TLS record-splitting proxy port."""
        return self._tls_proxy.port

    @property
    def last_error(self) -> str | None:
        """Return the most recent startup/runtime error message, if any."""
        return self._last_error

    def register_target_ip(self, ip_addr: str) -> None:
        """Mark an IPv4 address as belonging to a target domain."""
        with self._stats_lock:
            self._target_ips.add(ip_addr)

    def _record_tls_bypass(self, hostname: str) -> None:
        with self._stats_lock:
            self.stats.tls_fragmented += 1
            self.stats.last_domain = hostname

    def update_settings(self, settings: AppSettings) -> None:
        """Update active profile, target domain list, and DoH endpoints on the fly."""
        self.settings = settings
        self._doh.endpoints = list(settings.doh_endpoints)

    def start(self) -> tuple[bool, str]:
        """Start local TLS Record Proxy, open WinDivert handle, and flush poisoned DNS cache."""
        if self._running.is_set():
            return True, "FastCon motoru zaten çalışıyor."

        self._last_error = None
        self._has_global_ipv6 = _host_has_global_ipv6()
        proxy_port = self._tls_proxy.start()
        filter_expr = build_windivert_filter(proxy_port)

        try:
            handle = self._divert_factory(filter_expr)
            handle.open()
        except PermissionError:
            self._tls_proxy.stop()
            self._last_error = "Yönetici (Administrator) izni gerekli."
            return False, self._last_error
        except OSError as exc:
            self._tls_proxy.stop()
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

        # Flush Windows DNS cache and pre-warm DoH connection pool + core domains
        threading.Thread(
            target=self._bootstrap_dns_and_doh,
            name="fastcon-bootstrap",
            daemon=True,
        ).start()

        return True, "FastCon aktif — 0 ms oyun/ses gecikmesiyle koruma çalışıyor."

    def _bootstrap_dns_and_doh(self) -> None:
        """Flush stale OS DNS entries and pre-warm HTTP/2 DoH cache for instant first launch."""
        _flush_windows_dns_cache()
        if not self.settings.active_profile.enable_doh:
            return
        for domain in ("discord.com", "gateway.discord.gg", "www.roblox.com"):
            if not self._running.is_set():
                break
            try:
                q_wire = dns.message.make_query(domain, dns.rdatatype.A).to_wire()
                ans = self._doh.resolve_wire(q_wire)
                if ans is not None:
                    self._extract_and_track_ipv4_answers(ans)
            except (dns.exception.DNSException, ValueError, OSError):
                continue

    def stop(self) -> None:
        """Cleanly release WinDivert handle and local proxy so networking returns immediately to normal."""
        self._running.clear()
        handle = self._handle
        self._handle = None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

        self._tls_proxy.stop()

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
        """Redirect TLS handshakes to local record-splitter, resolve DoH DNS, and fragment HTTP."""
        try:
            if packet.udp is not None and packet.dst_port == 53:
                if async_dns and self._dns_pool is not None:
                    cloned = pydivert.Packet(
                        bytes(packet.raw),
                        interface=packet.interface,
                        direction=packet.direction,
                    )
                    self._dns_pool.submit(self.handle_dns_packet_sync, cloned, send_fn)
                else:
                    self.handle_dns_packet_sync(packet, send_fn)
                return

            if packet.tcp is not None:
                proxy_port = self._tls_proxy.port

                # 1. Return leg of hairpin NAT (TLSRecordProxy -> Local Application)
                if (
                    proxy_port >= 0
                    and packet.src_port == proxy_port
                    and proxy_port != 0
                ):
                    orig_port = self._tls_proxy.lookup_original_port(
                        packet.dst_addr, int(packet.dst_port)
                    )
                    rewritten = pydivert.Packet(
                        bytes(packet.raw),
                        interface=packet.interface,
                        direction=pydivert.Direction.INBOUND,
                    )
                    rewritten.src_addr, rewritten.dst_addr = (
                        packet.dst_addr,
                        packet.src_addr,
                    )
                    rewritten.src_port = orig_port
                    rewritten.recalculate_checksums()
                    send_fn(rewritten)
                    return

                # 2. Outbound TLS ports -> Hairpin redirect to TLSRecordProxy (when proxy is running
                #    or when SYN / target IP is detected)
                if packet.dst_port in _TLS_PORTS and packet.ipv4 is not None:
                    if self._tls_proxy.is_upstream_port(int(packet.src_port)):
                        send_fn(packet)
                        return

                    if proxy_port > 0 or packet.tcp.syn:
                        active_port = (
                            proxy_port if proxy_port > 0 else self._tls_proxy.start()
                        )
                        self._tls_proxy.record_redirect(
                            packet.dst_addr,
                            int(packet.src_port),
                            int(packet.dst_port),
                        )
                        hairpin = pydivert.Packet(
                            bytes(packet.raw),
                            interface=packet.interface,
                            direction=pydivert.Direction.INBOUND,
                        )
                        hairpin.src_addr, hairpin.dst_addr = (
                            packet.dst_addr,
                            packet.src_addr,
                        )
                        hairpin.dst_port = active_port
                        hairpin.recalculate_checksums()
                        send_fn(hairpin)
                        return

                    # Direct packet-level fallback when proxy is not running (e.g. unit test payloads)
                    if packet.payload:
                        self._handle_tls_packet_direct(packet, send_fn)
                        return

                # 3. Plaintext HTTP port 80 Host manipulation
                if packet.dst_port == 80 and packet.payload:
                    self._handle_http_packet(packet, send_fn)
                    return

            send_fn(packet)
        except (OSError, ValueError, IndexError, KeyError, RuntimeError):
            send_fn(packet)

    def _handle_tls_packet_direct(
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
        self._record_tls_bypass(sni_match.hostname)

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
        if original.tcp is None or (len(fragments) <= 1 and not fragments[0].is_fake):
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
        """Resolve matching outbound UDP/53 queries via DoH and inject clean INBOUND reply."""
        profile = self.settings.active_profile
        if not profile.enable_doh or not packet.payload:
            send_fn(packet)
            return

        query_bytes = bytes(packet.payload)
        question = extract_dns_question(query_bytes)
        if question is None or not self.settings.should_process_domain(question[0]):
            send_fn(packet)
            return

        qname, rdtype, _ = question

        # If host lacks global IPv6, synthesize immediate empty NOERROR for AAAA (28)
        # to prevent ISP router fe80::1 IPv6 sinkhole (2a01:358:...) and 300ms fallback lag.
        doh_answer: bytes | None = None
        if rdtype == int(dns.rdatatype.AAAA) and not self._has_global_ipv6:
            try:
                q_msg = dns.message.from_wire(query_bytes)
                doh_answer = dns.message.make_response(q_msg).to_wire()
            except (dns.exception.DNSException, ValueError):
                doh_answer = None
        else:
            doh_answer = self._doh.resolve_wire(query_bytes)
            if doh_answer is not None:
                self._extract_and_track_ipv4_answers(doh_answer)

        if doh_answer is None:
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
            self.stats.last_domain = qname

    def _extract_and_track_ipv4_answers(self, wire_answer: bytes) -> None:
        try:
            parsed = dns.message.from_wire(wire_answer)
            for rrset in parsed.answer:
                if rrset.rdtype == dns.rdatatype.A:
                    for rdata in rrset:
                        ip_text = str(rdata)
                        if not ip_text.startswith(BTK_SINKHOLE_PREFIX):
                            self.register_target_ip(ip_text)
        except (dns.exception.DNSException, ValueError):
            pass
