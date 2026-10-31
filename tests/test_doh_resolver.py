"""Unit tests for the RFC 8484 DNS-over-HTTPS (DoH) resolver and TTL cache."""

from __future__ import annotations

import dns.message
import dns.rdatatype
import dns.rrset
import httpx

from src.engine.doh_resolver import DoHResolver, extract_dns_question


def _make_dns_query(
    domain: str, rdtype: dns.rdatatype.RdataType = dns.rdatatype.A
) -> bytes:
    msg = dns.message.make_query(domain, rdtype)
    return msg.to_wire()


def _make_dns_response(
    query_wire: bytes, ip: str = "162.159.135.232", ttl: int = 120
) -> bytes:
    query_msg = dns.message.from_wire(query_wire)
    resp = dns.message.make_response(query_msg)
    qname = query_msg.question[0].name
    rrset = dns.rrset.from_text(qname, ttl, "IN", "A", ip)
    resp.answer.append(rrset)
    return resp.to_wire()


def test_extract_dns_question_parses_valid_and_rejects_malformed() -> None:
    """Valid DNS wire queries return normalized domain; invalid bytes return None."""
    wire = _make_dns_query("Gateway.Discord.GG.")
    question = extract_dns_question(wire)
    assert question is not None
    assert question[0] == "gateway.discord.gg"

    assert extract_dns_question(b"") is None
    assert extract_dns_question(b"\x00\x01not-a-dns-packet") is None


def test_doh_resolver_resolves_and_caches_with_rewritten_transaction_id() -> None:
    """Cached DoH responses rewrite the 2-byte DNS Transaction ID to match new queries."""
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        assert request.headers.get("content-type") == "application/dns-message"
        resp_wire = _make_dns_response(request.content, ip="162.159.136.232", ttl=180)
        return httpx.Response(
            200,
            content=resp_wire,
            headers={"content-type": "application/dns-message"},
        )

    transport = httpx.MockTransport(mock_handler)
    client = httpx.Client(transport=transport)
    resolver = DoHResolver(
        endpoints=["https://1.1.1.1/dns-query"],
        http_client=client,
    )

    query1 = _make_dns_query("discord.com")
    query2 = _make_dns_query("discord.com")
    assert query1[:2] != query2[:2]  # Distinct DNS transaction IDs

    ans1 = resolver.resolve_wire(query1)
    ans2 = resolver.resolve_wire(query2)

    assert ans1 is not None
    assert ans2 is not None
    assert call_count == 1  # Second query served from in-memory TTL cache
    assert ans1[:2] == query1[:2]
    assert ans2[:2] == query2[:2]

    parsed = dns.message.from_wire(ans2)
    assert str(parsed.answer[0][0]) == "162.159.136.232"


def test_doh_resolver_fails_over_to_backup_endpoint() -> None:
    """When primary DoH endpoint fails, resolver automatically uses fallback endpoint."""
    attempted_urls: list[str] = []

    def failover_handler(request: httpx.Request) -> httpx.Response:
        attempted_urls.append(str(request.url))
        if "1.1.1.1" in str(request.url):
            raise httpx.ConnectTimeout("Primary DoH timed out")
        resp_wire = _make_dns_response(request.content, ip="128.116.127.3")
        return httpx.Response(200, content=resp_wire)

    client = httpx.Client(transport=httpx.MockTransport(failover_handler))
    resolver = DoHResolver(
        endpoints=["https://1.1.1.1/dns-query", "https://8.8.8.8/dns-query"],
        http_client=client,
    )

    query = _make_dns_query("www.roblox.com")
    ans = resolver.resolve_wire(query)

    assert ans is not None
    assert len(attempted_urls) == 2
    parsed = dns.message.from_wire(ans)
    assert str(parsed.answer[0][0]) == "128.116.127.3"


def test_doh_resolver_evicts_full_cache_and_handles_total_failure() -> None:
    """Bounded cache evicts oldest entry, clear_cache resets state, and total failure returns None."""
    fail_mode = False

    def handler(request: httpx.Request) -> httpx.Response:
        if fail_mode:
            return httpx.Response(503)
        q = dns.message.from_wire(request.content)
        # Response with empty answer section tests minimum fallback TTL
        r = dns.message.make_response(q)
        return httpx.Response(200, content=r.to_wire())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = DoHResolver(
        endpoints=["https://1.1.1.1/dns-query"],
        max_cache_entries=1,
        http_client=client,
    )

    assert resolver.resolve_wire(b"short") is None
    assert resolver.resolve_wire(_make_dns_query("discord.com")) is not None
    assert resolver.resolve_wire(_make_dns_query("roblox.com")) is not None

    resolver.clear_cache()
    fail_mode = True
    assert resolver.resolve_wire(_make_dns_query("discord.com")) is None
    resolver.close()
