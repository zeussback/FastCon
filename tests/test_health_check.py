"""Unit tests for DPIService packet processing and Discord/Roblox health diagnostics."""

from __future__ import annotations

import struct

import dns.message
import dns.rdatatype
import dns.rrset
import httpx
import pydivert

from src.config import AppSettings, ScopeMode
from src.diagnostics.health_check import HealthChecker
from src.engine.doh_resolver import DoHResolver
from src.engine.dpi_service import DPIService


def _craft_client_hello(hostname: str) -> bytes:
    host_bytes = hostname.encode("ascii")
    sni_entry = struct.pack("!BH", 0x00, len(host_bytes)) + host_bytes
    sni_ext_data = struct.pack("!H", len(sni_entry)) + sni_entry
    extensions = struct.pack("!HH", 0x0000, len(sni_ext_data)) + sni_ext_data
    body = (
        b"\x03\x03"
        + (b"\xaa" * 32)
        + b"\x00"
        + struct.pack("!HH", 2, 0x1301)
        + b"\x01\x00"
        + struct.pack("!H", len(extensions))
        + extensions
    )
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + struct.pack("!H", len(handshake)) + handshake


def test_dpi_service_fragments_target_tls_and_leaves_unrelated_untouched() -> None:
    """DPIService fragments Discord TLS ClientHello into valid TCP packets and passes banking sites untouched."""
    sent_packets: list[pydivert.Packet] = []
    settings = AppSettings(profile_id="superonline", scope_mode=ScopeMode.SELECTED_ONLY)
    service = DPIService(settings=settings)

    # 1. Target domain: discord.com -> must be fragmented into multiple valid TCP packets
    discord_hello = _craft_client_hello("discord.com")
    discord_pkt = (
        pydivert.PacketBuilder()
        .ipv4("192.168.1.20", "162.159.135.232")
        .tcp(51000, 443)
        .payload(discord_hello)
        .build()
    )
    assert discord_pkt.tcp is not None
    discord_pkt.tcp.seq_num = 10_000
    discord_pkt.recalculate_checksums()

    service.process_packet(discord_pkt, send_fn=sent_packets.append)

    assert len(sent_packets) >= 2
    assert service.stats.tls_fragmented == 1
    for pkt in sent_packets:
        assert pkt.is_checksum_valid
        assert b"discord.com" not in pkt.payload

    # Reconstruct TCP stream by sequence number
    reassembled = b"".join(
        p.payload
        for p in sorted(sent_packets, key=lambda item: item.tcp.seq_num)  # type: ignore[union-attr]
    )
    assert reassembled == discord_hello

    # 2. Non-target domain: garanti.com.tr -> passed through untouched as 1 packet
    sent_packets.clear()
    bank_hello = _craft_client_hello("garanti.com.tr")
    bank_pkt = (
        pydivert.PacketBuilder()
        .ipv4("192.168.1.20", "195.87.10.1")
        .tcp(51001, 443)
        .payload(bank_hello)
        .build()
    )
    service.process_packet(bank_pkt, send_fn=sent_packets.append)
    assert len(sent_packets) == 1
    assert sent_packets[0].payload == bank_hello
    assert service.stats.tls_fragmented == 1


def test_dpi_service_resolves_target_dns_via_doh_inbound_injection() -> None:
    """Outbound UDP/53 queries for Roblox are resolved via DoH and injected back as INBOUND."""

    def mock_doh_handler(request: httpx.Request) -> httpx.Response:
        q = dns.message.from_wire(request.content)
        r = dns.message.make_response(q)
        r.answer.append(
            dns.rrset.from_text(q.question[0].name, 60, "IN", "A", "128.116.127.3")
        )
        return httpx.Response(200, content=r.to_wire())

    doh = DoHResolver(
        endpoints=["https://1.1.1.1/dns-query"],
        http_client=httpx.Client(transport=httpx.MockTransport(mock_doh_handler)),
    )
    settings = AppSettings(profile_id="superonline", scope_mode=ScopeMode.SELECTED_ONLY)
    service = DPIService(settings=settings, doh_resolver=doh)

    query_wire = dns.message.make_query("www.roblox.com", dns.rdatatype.A).to_wire()
    dns_pkt = (
        pydivert.PacketBuilder()
        .ipv4("192.168.1.20", "192.168.1.1")
        .udp(60555, 53)
        .payload(query_wire)
        .build()
    )

    injected: list[pydivert.Packet] = []
    service.handle_dns_packet_sync(dns_pkt, send_fn=injected.append)

    assert len(injected) == 1
    reply = injected[0]
    assert reply.direction == pydivert.Direction.INBOUND
    assert reply.src_addr == "192.168.1.1"
    assert reply.dst_addr == "192.168.1.20"
    assert reply.src_port == 53
    assert reply.dst_port == 60555
    assert reply.is_checksum_valid
    assert service.stats.dns_resolved == 1

    parsed_dns = dns.message.from_wire(reply.payload)
    assert str(parsed_dns.answer[0][0]) == "128.116.127.3"


def test_health_checker_reports_discord_and_roblox_statuses() -> None:
    """HealthChecker distinguishes reachable services from timed-out/blocked services."""

    def probe_transport(request: httpx.Request) -> httpx.Response:
        if "discord" in str(request.url):
            return httpx.Response(200)
        raise httpx.ConnectTimeout("ISP DPI timeout")

    client = httpx.Client(transport=httpx.MockTransport(probe_transport))
    checker = HealthChecker(http_client=client)
    results = checker.check_all()

    by_name = {item.service_name: item for item in results}
    assert by_name["Discord"].reachable is True
    assert by_name["Discord"].latency_ms is not None
    assert "Web + Ses" in by_name["Discord"].detail
    assert len(by_name["Discord"].endpoints) == 2

    assert by_name["Roblox"].reachable is False
    assert "Engel" in by_name["Roblox"].detail or "Zaman" in by_name["Roblox"].detail
    assert len(by_name["Roblox"].endpoints) == 2


def test_health_checker_composite_partial_failures() -> None:
    """HealthChecker detects partial ISP blocks (e.g. web accessible but voice gateway blocked)."""

    def mock_transport(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        # Discord: web is open, but gateway is blocked
        if "discord.com" in url_str:
            return httpx.Response(200)
        if "gateway.discord.gg" in url_str:
            raise httpx.ConnectTimeout("DPI timeout on gateway")
        # Roblox: web is open, but API is blocked
        if (
            "roblox.com/cdn" in url_str
            or url_str.rstrip("/") == "https://www.roblox.com"
        ):
            return httpx.Response(200)
        if "apis.roblox.com" in url_str:
            raise httpx.ConnectError("Connection reset by peer")
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(mock_transport))
    checker = HealthChecker(http_client=client)
    results = {item.service_name: item for item in checker.check_all()}

    # Discord partial failure
    discord = results["Discord"]
    assert discord.reachable is False
    assert discord.is_partial is True
    assert "Ses/Gateway Engelli" in discord.detail

    # Roblox partial failure
    roblox = results["Roblox"]
    assert roblox.reachable is False
    assert roblox.is_partial is True
    assert "Oyun API Engelli" in roblox.detail


def test_health_checker_classifies_dns_reset_and_5xx_responses() -> None:
    """HealthChecker distinguishes DNS errors, TCP connection resets, and 5xx server responses."""

    def varied_transport(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "dns-fail" in url:
            raise httpx.ConnectError("getaddrinfo failed")
        if "tcp-rst" in url:
            raise httpx.ConnectError("Connection reset by peer")
        if "proto-err" in url:
            raise httpx.RemoteProtocolError("Malformed response")
        return httpx.Response(502)

    checker = HealthChecker(
        probes=(
            ("DNSFail", "https://dns-fail.invalid/"),
            ("Reset", "https://tcp-rst.invalid/"),
            ("Proto", "https://proto-err.invalid/"),
            ("Server502", "https://502.invalid/"),
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(varied_transport)),
    )
    results = {r.service_name: r for r in checker.check_all()}
    assert results["DNSFail"].reachable is False
    assert "DNS" in results["DNSFail"].detail
    assert results["Reset"].reachable is False
    assert "DPI" in results["Reset"].detail
    assert results["Proto"].reachable is False
    assert results["Server502"].reachable is True
    assert "502" in results["Server502"].detail


def test_dpi_service_http_fragmentation_ttl_and_lifecycle() -> None:
    """DPIService fragments HTTP port 80, applies low-TTL decoy, and cleanly starts/stops."""
    settings = AppSettings(
        profile_id="superonline_ttl", scope_mode=ScopeMode.SELECTED_ONLY
    )

    sent_from_handle: list[pydivert.Packet] = []

    class FakeWinDivert:
        def __init__(self, filter_str: str) -> None:
            self.filter_str = filter_str
            self.opened = False
            self._emitted = False

        def open(self) -> None:
            self.opened = True

        def recv(self) -> pydivert.Packet:
            if not self._emitted:
                self._emitted = True
                http_payload = b"GET / HTTP/1.1\r\nHost: www.roblox.com\r\n\r\n"
                return (
                    pydivert.PacketBuilder()
                    .ipv4("192.168.1.20", "128.116.127.3", ttl=64)
                    .tcp(52000, 80)
                    .payload(http_payload)
                    .build()
                )
            raise OSError("Handle closed")

        def send(self, packet: pydivert.Packet) -> None:
            sent_from_handle.append(packet)

        def close(self) -> None:
            self.opened = False

    doh_fail = DoHResolver(
        endpoints=["https://1.1.1.1/dns-query"],
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(500))
        ),
    )
    service = DPIService(
        settings=settings,
        doh_resolver=doh_fail,
        divert_factory=FakeWinDivert,  # type: ignore[arg-type]
    )

    ok, _ = service.start()
    assert ok is True
    service.update_settings(settings)
    service.stop()
    assert service.is_running is False
    assert service.stats.http_fragmented == 1
    assert len(sent_from_handle) == 2

    # Verify low-TTL decoy emission on TLS with superonline_ttl profile
    tls_out: list[pydivert.Packet] = []
    hello_pkt = (
        pydivert.PacketBuilder()
        .ipv4("192.168.1.20", "162.159.135.232", ttl=64)
        .tcp(52001, 443)
        .payload(_craft_client_hello("discord.com"))
        .build()
    )
    service.process_packet(hello_pkt, send_fn=tls_out.append)
    assert any(p.ipv4 is not None and p.ipv4.ttl == 3 for p in tls_out)

    # Verify fail-safe DNS passthrough when DoH fails
    dns_out: list[pydivert.Packet] = []
    q_wire = dns.message.make_query("discord.com", dns.rdatatype.A).to_wire()
    q_pkt = (
        pydivert.PacketBuilder()
        .ipv4("192.168.1.20", "192.168.1.1")
        .udp(61000, 53)
        .payload(q_wire)
        .build()
    )
    service.handle_dns_packet_sync(q_pkt, send_fn=dns_out.append)
    assert len(dns_out) == 1
    assert dns_out[0].direction == pydivert.Direction.OUTBOUND


def test_dpi_service_hairpin_redirects_target_tls_to_local_record_proxy() -> None:
    """Outbound TCP 443 to target IPs or BTK sinkhole IP redirects cleanly to TLSRecordProxy."""
    settings = AppSettings(profile_id="superonline", scope_mode=ScopeMode.SELECTED_ONLY)
    service = DPIService(settings=settings)
    service.register_target_ip("162.159.135.232")

    # Simulate client SYN to Discord IP:443 -> must hairpin INBOUND to proxy port
    syn_pkt = (
        pydivert.PacketBuilder()
        .ipv4("192.168.1.5", "162.159.135.232")
        .tcp(53100, 443)
        .build()
    )
    assert syn_pkt.tcp is not None
    syn_pkt.tcp.syn = True
    syn_pkt.recalculate_checksums()

    out: list[pydivert.Packet] = []
    service.process_packet(syn_pkt, send_fn=out.append)

    assert len(out) == 1
    redirected = out[0]
    assert redirected.direction == pydivert.Direction.INBOUND
    assert redirected.src_addr == "162.159.135.232"
    assert redirected.dst_addr == "192.168.1.5"
    assert redirected.dst_port == service.proxy_port

    # Simulate proxy SYN-ACK back from proxy_port -> must hairpin INBOUND from port 443
    reply_pkt = (
        pydivert.PacketBuilder()
        .ipv4("192.168.1.5", "162.159.135.232")
        .tcp(service.proxy_port, 53100)
        .build()
    )
    out.clear()
    service.process_packet(reply_pkt, send_fn=out.append)

    assert len(out) == 1
    returned = out[0]
    assert returned.direction == pydivert.Direction.INBOUND
    assert returned.src_addr == "162.159.135.232"
    assert returned.src_port == 443
    assert returned.dst_addr == "192.168.1.5"
    assert returned.dst_port == 53100
    service.stop()


def test_health_checker_preinitializes_shared_ssl_context() -> None:
    """HealthChecker caches a reusable SSLContext so disk CA loading never inflates ping."""
    import ssl

    checker = HealthChecker()
    assert isinstance(checker.ssl_context, ssl.SSLContext)
