"""Unit tests for TLS ClientHello SNI parsing, HTTP Host parsing, and TCP fragmentation."""

from __future__ import annotations

import struct

from src.config import ISP_PROFILES
from src.engine.packet_parser import (
    build_http_fragments,
    build_tls_fragments,
    extract_http_host,
    extract_tls_sni,
)


def _craft_tls_client_hello(hostname: str) -> bytes:
    """Construct a valid RFC 5246 / RFC 6066 TLS ClientHello record containing SNI."""
    host_bytes = hostname.encode("ascii")
    # SNI extension payload: list_len (2) + name_type=0 (1) + name_len (2) + host_bytes
    sni_entry = struct.pack("!BH", 0x00, len(host_bytes)) + host_bytes
    sni_ext_data = struct.pack("!H", len(sni_entry)) + sni_entry
    # Prepend a dummy extension (e.g., supported_groups 0x000a) before SNI to test extension walking
    dummy_ext = struct.pack("!HH", 0x000A, 4) + b"\x00\x02\x00\x1d"
    sni_ext = struct.pack("!HH", 0x0000, len(sni_ext_data)) + sni_ext_data
    extensions = dummy_ext + sni_ext

    client_version = b"\x03\x03"
    random_bytes = b"\x11" * 32
    session_id = b"\x22" * 32
    cipher_suites = struct.pack("!HH", 2, 0x1301)  # length 2, TLS_AES_128_GCM_SHA256
    compression = b"\x01\x00"  # length 1, null compression

    body = (
        client_version
        + random_bytes
        + struct.pack("!B", len(session_id))
        + session_id
        + cipher_suites
        + compression
        + struct.pack("!H", len(extensions))
        + extensions
    )

    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    record = b"\x16\x03\x01" + struct.pack("!H", len(handshake)) + handshake
    return record


def test_extract_tls_sni_parses_discord_and_roblox_handshakes() -> None:
    """Valid TLS ClientHello payloads return exact SNI hostname and byte offset."""
    for domain in ("discord.com", "gateway.discord.gg", "www.roblox.com"):
        payload = _craft_tls_client_hello(domain)
        match = extract_tls_sni(payload)
        assert match is not None
        assert match.hostname == domain
        assert payload[
            match.host_offset : match.host_offset + match.host_length
        ] == domain.encode("ascii")


def test_extract_tls_sni_handles_malformed_or_non_tls_safely() -> None:
    """Truncated, empty, or non-ClientHello payloads return None without raising."""
    assert extract_tls_sni(b"") is None
    assert extract_tls_sni(b"GET / HTTP/1.1\r\nHost: discord.com\r\n\r\n") is None
    assert extract_tls_sni(b"\x16\x03\x01\x00\x05hello") is None

    valid = _craft_tls_client_hello("discord.com")
    # Truncate right inside the SNI extension
    assert extract_tls_sni(valid[:-4]) is None


def test_build_tls_fragments_superonline_splits_sni_and_reverses_order() -> None:
    """Superonline profile splits inside the SNI string, reverses segments, and preserves stream."""
    payload = _craft_tls_client_hello("discord.com")
    sni_match = extract_tls_sni(payload)
    assert sni_match is not None

    profile = ISP_PROFILES["superonline"]
    fragments = build_tls_fragments(payload, profile=profile, sni_match=sni_match)

    real_fragments = [f for f in fragments if not f.is_fake]
    assert len(real_fragments) >= 2

    # In reverse mode, the first sent fragment has a higher sequence offset than the last
    assert real_fragments[0].seq_offset > real_fragments[-1].seq_offset

    # No single real fragment may contain the intact plaintext SNI "discord.com"
    for frag in real_fragments:
        assert b"discord.com" not in frag.payload

    # Reassembling real fragments sorted by sequence offset reconstructs the exact original bytes
    reassembled = b"".join(
        f.payload for f in sorted(real_fragments, key=lambda item: item.seq_offset)
    )
    assert reassembled == payload


def test_build_tls_fragments_aggressive_includes_low_ttl_fake_packet() -> None:
    """Superonline Aggressive profile prepends a low-TTL fake packet while keeping stream intact."""
    payload = _craft_tls_client_hello("www.roblox.com")
    sni_match = extract_tls_sni(payload)
    profile = ISP_PROFILES["superonline_ttl"]

    fragments = build_tls_fragments(payload, profile=profile, sni_match=sni_match)
    fake_fragments = [f for f in fragments if f.is_fake]
    real_fragments = [f for f in fragments if not f.is_fake]

    assert len(fake_fragments) == 1
    assert fake_fragments[0].ttl == profile.fake_packet_ttl

    reassembled = b"".join(
        f.payload for f in sorted(real_fragments, key=lambda item: item.seq_offset)
    )
    assert reassembled == payload


def test_extract_and_fragment_http_host_header() -> None:
    """HTTP requests extract Host header, mix Host: casing, and split hostname across segments."""
    http_req = (
        b"GET /login HTTP/1.1\r\n"
        b"Host: www.roblox.com\r\n"
        b"User-Agent: Roblox/WinInet\r\n\r\n"
    )
    host = extract_http_host(http_req)
    assert host == "www.roblox.com"

    fragments = build_http_fragments(http_req, profile=ISP_PROFILES["superonline"])
    assert len(fragments) == 2

    for frag in fragments:
        assert b"www.roblox.com" not in frag.payload

    reassembled = b"".join(
        f.payload for f in sorted(fragments, key=lambda item: item.seq_offset)
    )
    assert b"hoSt: www.roblox.com" in reassembled
    assert len(reassembled) == len(http_req)
