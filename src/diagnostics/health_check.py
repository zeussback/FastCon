"""Live reachability and true 1-RTT ping diagnostics for Discord and Roblox."""

from __future__ import annotations

import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlsplit

import certifi
import httpx

DEFAULT_PROBES: tuple[tuple[str, str], ...] = (
    ("Discord", "https://discord.com/cdn-cgi/trace"),
    ("Roblox", "https://www.roblox.com/"),
)


@dataclass(frozen=True)
class ServiceHealth:
    """Result of a single live connectivity and network latency check."""

    service_name: str
    url: str
    reachable: bool
    latency_ms: int | None
    detail: str


class HealthChecker:
    """Tests DNS + verified TLS/HTTPS reachability and measures 1-RTT server ping."""

    def __init__(
        self,
        probes: tuple[tuple[str, str], ...] = DEFAULT_PROBES,
        timeout_seconds: float = 3.5,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.probes = probes
        self._timeout = timeout_seconds
        self._client = http_client
        # Pre-load CA bundle once so disk I/O never inflates live latency measurements
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

    def check_single(self, service_name: str, url: str) -> ServiceHealth:
        """Probe an HTTPS endpoint and report verified reachability with 1-RTT network ping."""
        if self._client is not None:
            return self._check_via_injected_client(service_name, url)
        return self._check_via_verified_tls_socket(service_name, url)

    def _check_via_injected_client(self, service_name: str, url: str) -> ServiceHealth:
        started = time.perf_counter()
        assert self._client is not None
        try:
            response = self._client.get(url)
            elapsed_ms = max(1, round((time.perf_counter() - started) * 1000))
            if response.status_code < 500:
                return ServiceHealth(
                    service_name=service_name,
                    url=url,
                    reachable=True,
                    latency_ms=elapsed_ms,
                    detail=f"Erişilebilir ({elapsed_ms} ms)",
                )
            return ServiceHealth(
                service_name=service_name,
                url=url,
                reachable=True,
                latency_ms=elapsed_ms,
                detail=f"Sunucu Yanıt Verdi — HTTP {response.status_code} ({elapsed_ms} ms)",
            )
        except httpx.TimeoutException:
            return ServiceHealth(
                service_name=service_name,
                url=url,
                reachable=False,
                latency_ms=None,
                detail="DPI Engeli / Zaman Aşımı",
            )
        except httpx.ConnectError as exc:
            err_text = str(exc).lower()
            if "getaddrinfo" in err_text or "name or service not known" in err_text:
                reason = "DNS Engeli / Çözümlenemedi"
            else:
                reason = "Bağlantı Sıfırlandı (DPI Engeli)"
            return ServiceHealth(
                service_name=service_name,
                url=url,
                reachable=False,
                latency_ms=None,
                detail=reason,
            )
        except httpx.HTTPError as exc:
            return ServiceHealth(
                service_name=service_name,
                url=url,
                reachable=False,
                latency_ms=None,
                detail=f"Bağlantı Hatası ({type(exc).__name__})",
            )

    def _check_via_verified_tls_socket(
        self, service_name: str, url: str
    ) -> ServiceHealth:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = parsed.port or 443
        path = parsed.path or "/"

        try:
            addrs = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            if not addrs:
                return ServiceHealth(
                    service_name=service_name,
                    url=url,
                    reachable=False,
                    latency_ms=None,
                    detail="DNS Engeli / Çözümlenemedi",
                )
            target_ip = str(addrs[0][4][0])
        except socket.gaierror:
            return ServiceHealth(
                service_name=service_name,
                url=url,
                reachable=False,
                latency_ms=None,
                detail="DNS Engeli / Çözümlenemedi",
            )

        try:
            t_tcp_start = time.perf_counter()
            with socket.create_connection(
                (target_ip, port), timeout=self._timeout
            ) as raw_sock:
                tcp_ms = (time.perf_counter() - t_tcp_start) * 1000
                raw_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                t_tls_start = time.perf_counter()
                with self.ssl_context.wrap_socket(
                    raw_sock, server_hostname=host
                ) as tls_sock:
                    tls_ms = (time.perf_counter() - t_tls_start) * 1000
                    req = (
                        f"HEAD {path} HTTP/1.1\r\n"
                        f"Host: {host}\r\n"
                        "User-Agent: FastCon-Diagnostics/0.1\r\n"
                        "Connection: close\r\n\r\n"
                    ).encode("ascii")
                    tls_sock.sendall(req)
                    first_bytes = tls_sock.recv(256)

            if not first_bytes or not first_bytes.startswith(b"HTTP/"):
                return ServiceHealth(
                    service_name=service_name,
                    url=url,
                    reachable=False,
                    latency_ms=None,
                    detail="Bağlantı Sıfırlandı (DPI Engeli)",
                )

            # When hairpinned via local TLSRecordProxy, tcp_ms is <5ms (local) and tls_ms
            # spans upstream TCP SYN-ACK (1 RTT) + TLS 1.3 ServerHello (1 RTT) = 2 RTTs.
            ping_ms = max(1, round(tls_ms / 2) if tcp_ms < 5.0 else round(tcp_ms))

            status_parts = first_bytes.splitlines()[0].split()
            status_code = (
                int(status_parts[1])
                if len(status_parts) >= 2 and status_parts[1].isdigit()
                else 200
            )
            if status_code < 500:
                return ServiceHealth(
                    service_name=service_name,
                    url=url,
                    reachable=True,
                    latency_ms=ping_ms,
                    detail=f"Erişilebilir ({ping_ms} ms)",
                )
            return ServiceHealth(
                service_name=service_name,
                url=url,
                reachable=True,
                latency_ms=ping_ms,
                detail=f"Sunucu Yanıt Verdi — HTTP {status_code} ({ping_ms} ms)",
            )
        except TimeoutError:
            return ServiceHealth(
                service_name=service_name,
                url=url,
                reachable=False,
                latency_ms=None,
                detail="DPI Engeli / Zaman Aşımı",
            )
        except ConnectionResetError:
            return ServiceHealth(
                service_name=service_name,
                url=url,
                reachable=False,
                latency_ms=None,
                detail="Bağlantı Sıfırlandı (DPI Engeli)",
            )
        except OSError as exc:
            return ServiceHealth(
                service_name=service_name,
                url=url,
                reachable=False,
                latency_ms=None,
                detail=f"Bağlantı Hatası ({type(exc).__name__})",
            )

    def check_all(self) -> list[ServiceHealth]:
        """Run all service probes concurrently."""
        if self._client is not None:
            return [self.check_single(name, url) for name, url in self.probes]

        with ThreadPoolExecutor(max_workers=max(1, len(self.probes))) as pool:
            futures = [
                pool.submit(self.check_single, name, url) for name, url in self.probes
            ]
            return [f.result() for f in futures]
