"""Live reachability and true 1-RTT ping diagnostics for Discord and Roblox."""

from __future__ import annotations

import socket
import ssl
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlsplit

import certifi
import httpx


@dataclass(frozen=True)
class EndpointHealth:
    """Result of an individual endpoint check (e.g. Gateway or API)."""

    name: str
    url: str
    reachable: bool
    latency_ms: int | None
    detail: str


@dataclass(frozen=True)
class ServiceTarget:
    """Group of endpoints that constitute an entire user service."""

    service_name: str
    endpoints: tuple[tuple[str, str], ...]


DEFAULT_SERVICE_TARGETS: tuple[ServiceTarget, ...] = (
    ServiceTarget(
        service_name="Discord",
        endpoints=(
            ("Web & API", "https://discord.com/cdn-cgi/trace"),
            ("Ses & Gateway", "https://gateway.discord.gg/"),
        ),
    ),
    ServiceTarget(
        service_name="Roblox",
        endpoints=(
            ("Web", "https://www.roblox.com/"),
            ("Oyun API", "https://apis.roblox.com/"),
        ),
    ),
)

DEFAULT_PROBES: tuple[tuple[str, str], ...] = tuple(
    (t.service_name, t.endpoints[0][1]) for t in DEFAULT_SERVICE_TARGETS
)


@dataclass(frozen=True)
class ServiceHealth:
    """Result of a service health check bundling all constituent endpoints."""

    service_name: str
    url: str
    reachable: bool
    latency_ms: int | None
    detail: str
    endpoints: tuple[EndpointHealth, ...] = ()
    is_partial: bool = False


class HealthChecker:
    """Tests DNS + verified TLS/HTTPS reachability and measures 1-RTT server ping."""

    def __init__(
        self,
        targets: tuple[ServiceTarget, ...] | None = None,
        probes: Sequence[tuple[str, str]] | None = None,
        timeout_seconds: float = 3.5,
        http_client: httpx.Client | None = None,
    ) -> None:
        if targets is not None:
            self.targets = targets
        elif probes is not None:
            self.targets = tuple(
                ServiceTarget(service_name=name, endpoints=((name, url),))
                for name, url in probes
            )
        else:
            self.targets = DEFAULT_SERVICE_TARGETS

        self.probes = tuple((t.service_name, t.endpoints[0][1]) for t in self.targets)
        self._timeout = timeout_seconds
        self._client = http_client
        # Pre-load CA bundle once so disk I/O never inflates live latency measurements
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

    def check_endpoint(self, name: str, url: str) -> EndpointHealth:
        """Probe an individual endpoint and return its verified connectivity."""
        if self._client is not None:
            res = self._check_via_injected_client(name, url)
        else:
            res = self._check_via_verified_tls_socket(name, url)
        return EndpointHealth(
            name=name,
            url=url,
            reachable=res.reachable,
            latency_ms=res.latency_ms,
            detail=res.detail,
        )

    def check_single(self, service_name: str, url: str) -> ServiceHealth:
        """Probe a single endpoint and package it as a ServiceHealth result."""
        ep = self.check_endpoint(service_name, url)
        return ServiceHealth(
            service_name=service_name,
            url=url,
            reachable=ep.reachable,
            latency_ms=ep.latency_ms,
            detail=ep.detail,
            endpoints=(ep,),
            is_partial=False,
        )

    def check_target(
        self,
        target: ServiceTarget,
        pool: ThreadPoolExecutor | None = None,
    ) -> ServiceHealth:
        """Concurrently check all endpoints of a service and aggregate overall health."""
        if pool is not None and len(target.endpoints) > 1:
            futures = [
                pool.submit(self.check_endpoint, name, url)
                for name, url in target.endpoints
            ]
            ep_results = [f.result() for f in futures]
        else:
            ep_results = [
                self.check_endpoint(name, url) for name, url in target.endpoints
            ]

        reachable_eps = [e for e in ep_results if e.reachable]
        all_reachable = len(reachable_eps) == len(ep_results)
        any_reachable = len(reachable_eps) > 0
        primary_url = target.endpoints[0][1]

        if all_reachable:
            latencies = [e.latency_ms for e in ep_results if e.latency_ms is not None]
            ping_ms = max(latencies) if latencies else None
            if len(ep_results) == 1:
                detail = ep_results[0].detail
            elif target.service_name == "Discord":
                detail = f"Web + Ses Hazır ({ping_ms} ms)"
            elif target.service_name == "Roblox":
                detail = f"Web + Oyun Hazır ({ping_ms} ms)"
            else:
                detail = f"Erişilebilir ({ping_ms} ms)"

            return ServiceHealth(
                service_name=target.service_name,
                url=primary_url,
                reachable=True,
                latency_ms=ping_ms,
                detail=detail,
                endpoints=tuple(ep_results),
                is_partial=False,
            )

        if any_reachable:
            latencies = [
                e.latency_ms for e in reachable_eps if e.latency_ms is not None
            ]
            ping_ms = latencies[0] if latencies else None
            failed_names = [e.name for e in ep_results if not e.reachable]

            if target.service_name == "Discord" and any(
                "Ses" in n or "Gateway" in n for n in failed_names
            ):
                detail = "Kısmi: Web Açık, Ses/Gateway Engelli"
            elif target.service_name == "Roblox" and any(
                "API" in n or "Oyun" in n for n in failed_names
            ):
                detail = "Kısmi: Web Açık, Oyun API Engelli"
            else:
                detail = f"Kısmi: {', '.join(failed_names)} Engelli"

            return ServiceHealth(
                service_name=target.service_name,
                url=primary_url,
                reachable=False,
                latency_ms=ping_ms,
                detail=detail,
                endpoints=tuple(ep_results),
                is_partial=True,
            )

        # Total failure: none of the endpoints could connect
        return ServiceHealth(
            service_name=target.service_name,
            url=primary_url,
            reachable=False,
            latency_ms=None,
            detail=ep_results[0].detail,
            endpoints=tuple(ep_results),
            is_partial=False,
        )

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
                        "User-Agent: FastCon-Diagnostics/0.2\r\n"
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
        """Run all target service checks concurrently across all constituent endpoints."""
        total_endpoints = sum(len(t.endpoints) for t in self.targets)
        workers = max(1, min(total_endpoints, 12))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(self.check_target, target, pool) for target in self.targets
            ]
            return [f.result() for f in futures]
