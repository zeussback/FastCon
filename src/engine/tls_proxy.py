"""Local transparent TLS Record-Splitting TCP splice for stateful ISP DPI bypass."""

from __future__ import annotations

import logging
import socket
import threading
from collections.abc import Callable

import dns.exception
import dns.message
import dns.rdatatype

from src.app_logging import RateLimitedLog
from src.config import AppSettings
from src.engine.doh_resolver import DoHResolver
from src.engine.packet_parser import extract_tls_sni, split_tls_client_hello_records

logger = logging.getLogger(__name__)
_throttled = RateLimitedLog(logger)

BTK_SINKHOLE_PREFIX = "195.175.254."


class TLSRecordProxy:
    """Transparent local TCP splice that splits TLS ClientHello records across the SNI boundary.

    Operates strictly on TCP connections redirected via WinDivert hairpin NAT.
    Never touches UDP gaming or voice packets.
    """

    def __init__(
        self,
        settings_getter: Callable[[], AppSettings],
        doh_resolver: DoHResolver,
        on_tls_bypassed: Callable[[str], None] | None = None,
    ) -> None:
        self._get_settings = settings_getter
        self._doh = doh_resolver
        self._on_tls_bypassed = on_tls_bypassed

        self._server_sock: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._running = threading.Event()
        self._port: int = 0

        self._lock = threading.Lock()
        self._upstream_ports: set[int] = set()
        # Maps (remote_ip_seen_as_peer, client_src_port) -> original_dst_port
        self._orig_ports: dict[tuple[str, int], int] = {}

    @property
    def port(self) -> int:
        """Return the active local TCP port bound by this proxy (0 when stopped)."""
        return self._port

    def is_upstream_port(self, src_port: int) -> bool:
        """Return True if src_port belongs to one of this proxy's own outbound sockets."""
        with self._lock:
            return src_port in self._upstream_ports

    def record_redirect(
        self, remote_ip: str, client_port: int, orig_dst_port: int
    ) -> None:
        """Remember the original destination TCP port for a hairpinned client connection."""
        with self._lock:
            if len(self._orig_ports) > 4096:
                self._orig_ports.clear()
            self._orig_ports[(remote_ip, client_port)] = orig_dst_port

    def lookup_original_port(self, remote_ip: str, client_port: int) -> int:
        """Retrieve the original destination TCP port for return hairpin packets."""
        with self._lock:
            return self._orig_ports.get((remote_ip, client_port), 443)

    def start(self) -> int:
        """Bind an ephemeral local TCP port and start accepting hairpinned connections."""
        if self._running.is_set() and self._port > 0:
            return self._port

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", 0))
        srv.listen(256)
        self._port = int(srv.getsockname()[1])
        self._server_sock = srv
        self._running.set()

        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name="fastcon-tls-proxy",
            daemon=True,
        )
        self._accept_thread.start()
        return self._port

    def stop(self) -> None:
        """Close the listening socket and clear tracked port tables."""
        self._running.clear()
        srv = self._server_sock
        self._server_sock = None
        self._port = 0
        if srv is not None:
            try:
                srv.close()
            except OSError:
                pass
        with self._lock:
            self._upstream_ports.clear()
            self._orig_ports.clear()

    def _accept_loop(self) -> None:
        while self._running.is_set():
            srv = self._server_sock
            if srv is None:
                break
            try:
                cli_sock, addr = srv.accept()
            except OSError:
                break
            threading.Thread(
                target=self._handle_connection,
                args=(cli_sock, addr),
                name="fastcon-tls-conn",
                daemon=True,
            ).start()

    def _resolve_clean_ipv4(self, hostname: str, fallback_ip: str) -> str:
        """Resolve clean IPv4 via DoH if the client had a stale BTK sinkhole IP cached."""
        try:
            query_wire = dns.message.make_query(hostname, dns.rdatatype.A).to_wire()
            raw_ans = self._doh.resolve_wire(query_wire)
            if not raw_ans:
                return fallback_ip
            parsed = dns.message.from_wire(raw_ans)
            for rrset in parsed.answer:
                if rrset.rdtype == dns.rdatatype.A:
                    for rdata in rrset:
                        return str(rdata)
        except (dns.exception.DNSException, ValueError, OSError):
            logger.warning("Temiz IP DoH ile çözülemedi: %s", hostname, exc_info=True)
            return fallback_ip
        return fallback_ip

    def _handle_connection(
        self,
        cli_sock: socket.socket,
        addr: tuple[str, int],
    ) -> None:
        peer_ip, client_port = addr[0], int(addr[1])
        orig_dst_port = self.lookup_original_port(peer_ip, client_port)
        hostname = "?"
        target_ip = peer_ip

        up_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        local_port = 0
        try:
            cli_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            up_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            up_sock.bind(("0.0.0.0", 0))
            local_port = int(up_sock.getsockname()[1])
            with self._lock:
                self._upstream_ports.add(local_port)

            cli_sock.settimeout(5.0)
            initial_bytes = cli_sock.recv(32768)
            if not initial_bytes:
                return

            settings = self._get_settings()
            sni_match = extract_tls_sni(initial_bytes)
            outbound_first = initial_bytes
            if sni_match is not None:
                hostname = sni_match.hostname

            if sni_match is not None and settings.should_process_domain(
                sni_match.hostname
            ):
                if target_ip.startswith(BTK_SINKHOLE_PREFIX):
                    target_ip = self._resolve_clean_ipv4(sni_match.hostname, target_ip)
                    logger.info(
                        "Engelleme (sinkhole) IP'si düzeltildi: %s %s -> %s",
                        hostname,
                        peer_ip,
                        target_ip,
                    )
                outbound_first = split_tls_client_hello_records(
                    initial_bytes,
                    profile=settings.active_profile,
                    sni_match=sni_match,
                )
                if self._on_tls_bypassed is not None:
                    self._on_tls_bypassed(sni_match.hostname)
            elif target_ip.startswith(BTK_SINKHOLE_PREFIX) and sni_match is not None:
                # Even if not in selected domains, never connect to a poisoned sinkhole if SNI is present
                pass

            up_sock.settimeout(5.0)
            up_sock.connect((target_ip, orig_dst_port))
            cli_sock.settimeout(None)
            up_sock.settimeout(None)

            up_sock.sendall(outbound_first)

            up_to_cli = threading.Thread(
                target=self._pump,
                args=(up_sock, cli_sock),
                daemon=True,
            )
            up_to_cli.start()
            self._pump(cli_sock, up_sock)
            up_to_cli.join(timeout=2.0)
        except OSError as exc:
            _throttled.log(
                logging.INFO,
                f"conn:{hostname}:{type(exc).__name__}",
                "TLS bağlantısı kurulamadı: %s -> %s:%s (%s: %s)",
                hostname,
                target_ip,
                orig_dst_port,
                type(exc).__name__,
                exc,
            )
        finally:
            for s in (cli_sock, up_sock):
                try:
                    s.close()
                except OSError:
                    pass
            if local_port:
                with self._lock:
                    self._upstream_ports.discard(local_port)
                    self._orig_ports.pop((peer_ip, client_port), None)

    @staticmethod
    def _pump(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                chunk = src.recv(65536)
                if not chunk:
                    break
                dst.sendall(chunk)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass
