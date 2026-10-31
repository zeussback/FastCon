"""Thread-safe RFC 8484 DNS-over-HTTPS (DoH) resolver with in-memory TTL cache."""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass

import dns.exception
import dns.message
import httpx

from src.config import DEFAULT_DOH_ENDPOINTS, normalize_domain

CacheKey = tuple[str, int, int]
_DOH_MIME = "application/dns-message"


@dataclass
class _CacheEntry:
    wire_body_without_id: bytes
    expires_at: float


def extract_dns_question(wire_query: bytes) -> CacheKey | None:
    """Parse a raw DNS query packet and return (normalized_qname, rdtype, rdclass)."""
    if len(wire_query) < 12:
        return None
    try:
        msg = dns.message.from_wire(wire_query)
    except (dns.exception.DNSException, ValueError, IndexError):
        return None

    # QR bit must be 0 (query, not response) and at least one question must exist
    if (msg.flags & dns.flags.QR) != 0 or not msg.question:
        return None

    q = msg.question[0]
    qname = normalize_domain(q.name.to_text())
    if not qname:
        return None
    return qname, int(q.rdtype), int(q.rdclass)


def _compute_min_ttl(
    wire_response: bytes, default_ttl: int = 60, max_ttl: int = 600
) -> int:
    """Extract a safe cache TTL (clamped between 15s and max_ttl) from a DNS wire answer."""
    try:
        msg = dns.message.from_wire(wire_response)
        ttls = [rrset.ttl for rrset in msg.answer if rrset.ttl > 0]
        if not ttls:
            return 15
        return max(15, min(min(ttls), max_ttl))
    except (dns.exception.DNSException, ValueError):
        return default_ttl


class DoHResolver:
    """Resolves raw DNS wire queries over encrypted HTTPS with failover and TTL caching."""

    def __init__(
        self,
        endpoints: Sequence[str] = DEFAULT_DOH_ENDPOINTS,
        timeout_seconds: float = 2.5,
        max_cache_entries: int = 512,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.endpoints = list(endpoints) if endpoints else list(DEFAULT_DOH_ENDPOINTS)
        self._max_cache_entries = max_cache_entries
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            http2=True,
            timeout=timeout_seconds,
            headers={
                "accept": _DOH_MIME,
                "content-type": _DOH_MIME,
            },
        )
        self._cache: dict[CacheKey, _CacheEntry] = {}
        self._lock = threading.Lock()

    def resolve_wire(self, wire_query: bytes) -> bytes | None:
        """Resolve a raw DNS query packet via cache or DoH, preserving its 2-byte Transaction ID."""
        if len(wire_query) < 12:
            return None

        tx_id = wire_query[:2]
        question_key = extract_dns_question(wire_query)
        if question_key is None:
            return None

        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(question_key)
            if cached is not None:
                if cached.expires_at > now:
                    return tx_id + cached.wire_body_without_id
                self._cache.pop(question_key, None)

        # RFC 8484 Section 4.1 recommends DNS ID 0x0000 for HTTP caching compatibility
        doh_payload = b"\x00\x00" + wire_query[2:]
        headers = {"accept": _DOH_MIME, "content-type": _DOH_MIME}

        for endpoint in self.endpoints:
            try:
                response = self._client.post(
                    endpoint, content=doh_payload, headers=headers
                )
                if response.status_code == 200 and len(response.content) >= 12:
                    raw_answer = response.content
                    ttl = _compute_min_ttl(raw_answer)
                    body_without_id = raw_answer[2:]

                    with self._lock:
                        if len(self._cache) >= self._max_cache_entries:
                            # Evict oldest entry to keep memory bounded
                            oldest_key = next(iter(self._cache))
                            self._cache.pop(oldest_key, None)
                        self._cache[question_key] = _CacheEntry(
                            wire_body_without_id=body_without_id,
                            expires_at=time.monotonic() + ttl,
                        )
                    return tx_id + body_without_id
            except httpx.HTTPError:
                continue

        return None

    def clear_cache(self) -> None:
        """Clear all cached DNS records."""
        with self._lock:
            self._cache.clear()

    def close(self) -> None:
        """Close the underlying HTTP/2 connection pool if owned by this resolver."""
        if self._owns_client:
            self._client.close()
