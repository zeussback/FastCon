"""Live reachability and latency diagnostics for Discord and Roblox."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx

DEFAULT_PROBES: tuple[tuple[str, str], ...] = (
    ("Discord", "https://discord.com/api/v9/experiments"),
    ("Roblox", "https://www.roblox.com/"),
)


@dataclass(frozen=True)
class ServiceHealth:
    """Result of a single live connectivity check."""

    service_name: str
    url: str
    reachable: bool
    latency_ms: int | None
    detail: str


class HealthChecker:
    """Tests DNS + TLS/HTTPS handshake reachability to blocked target platforms."""

    def __init__(
        self,
        probes: tuple[tuple[str, str], ...] = DEFAULT_PROBES,
        timeout_seconds: float = 3.5,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.probes = probes
        self._timeout = timeout_seconds
        self._client = http_client

    def check_single(self, service_name: str, url: str) -> ServiceHealth:
        """Probe a single HTTPS endpoint and measure total handshake + response latency."""
        started = time.perf_counter()
        try:
            if self._client is not None:
                response = self._client.get(url)
            else:
                with httpx.Client(
                    timeout=self._timeout,
                    follow_redirects=False,
                    headers={"User-Agent": "FastCon-Diagnostics/0.1"},
                ) as temp_client:
                    response = temp_client.get(url)

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

    def check_all(self) -> list[ServiceHealth]:
        """Run all service probes concurrently so the UI updates in ~1-3 seconds max."""
        if self._client is not None:
            return [self.check_single(name, url) for name, url in self.probes]

        with ThreadPoolExecutor(max_workers=max(1, len(self.probes))) as pool:
            futures = [
                pool.submit(self.check_single, name, url) for name, url in self.probes
            ]
            return [f.result() for f in futures]
