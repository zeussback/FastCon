"""TLS ClientHello (SNI) and HTTP Host parser and zero-latency TCP fragmenter."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

from src.config import DPIProfile

_HTTP_METHODS = (
    b"GET ",
    b"POST ",
    b"HEAD ",
    b"PUT ",
    b"DELETE ",
    b"OPTIONS ",
    b"PATCH ",
)
_HTTP_HOST_RE = re.compile(rb"\r\n(Host)\s*:\s*([^\r\n]+)", re.IGNORECASE)


@dataclass(frozen=True)
class SNIMatch:
    """Extracted TLS Server Name Indication hostname and its byte span inside payload."""

    hostname: str
    host_offset: int
    host_length: int


@dataclass(frozen=True)
class TCPFragment:
    """Represents a single outbound TCP payload segment with relative sequence offset."""

    payload: bytes
    seq_offset: int
    is_fake: bool = False
    ttl: int | None = None


def extract_tls_sni(payload: bytes) -> SNIMatch | None:
    """Extract the SNI hostname and byte offset from a TLS ClientHello TCP payload.

    Returns None safely on non-TLS, encrypted, malformed, or truncated payloads.
    """
    total_len = len(payload)
    # Minimum TLS record (5) + Handshake header (4) + Version (2) + Random (32) + SessionID len (1) = 44
    if total_len < 44:
        return None

    # ContentType 0x16 (Handshake), TLS Major Version 0x03, HandshakeType 0x01 (ClientHello)
    if payload[0] != 0x16 or payload[1] != 0x03 or payload[5] != 0x01:
        return None

    # Skip Record Header (5) + Handshake Header (4) + Client Version (2) + Random (32) = 43
    pos = 43
    session_id_len = payload[pos]
    pos += 1 + session_id_len
    if pos + 2 > total_len:
        return None

    cipher_suites_len = struct.unpack_from("!H", payload, pos)[0]
    pos += 2 + cipher_suites_len
    if pos + 1 > total_len:
        return None

    compression_methods_len = payload[pos]
    pos += 1 + compression_methods_len
    if pos + 2 > total_len:
        return None

    extensions_total_len = struct.unpack_from("!H", payload, pos)[0]
    pos += 2
    extensions_end = min(total_len, pos + extensions_total_len)

    while pos + 4 <= extensions_end:
        ext_type, ext_len = struct.unpack_from("!HH", payload, pos)
        pos += 4
        if pos + ext_len > extensions_end:
            return None

        if ext_type == 0x0000:  # server_name extension
            if ext_len < 5:
                return None
            sni_list_len = struct.unpack_from("!H", payload, pos)[0]
            list_pos = pos + 2
            list_end = min(pos + 2 + sni_list_len, pos + ext_len)

            while list_pos + 3 <= list_end:
                name_type = payload[list_pos]
                name_len = struct.unpack_from("!H", payload, list_pos + 1)[0]
                host_start = list_pos + 3
                if host_start + name_len > list_end:
                    return None
                if name_type == 0x00 and name_len > 0:
                    raw_host = payload[host_start : host_start + name_len]
                    try:
                        hostname = raw_host.decode("ascii").strip().lower()
                    except UnicodeDecodeError:
                        return None
                    return SNIMatch(
                        hostname=hostname,
                        host_offset=host_start,
                        host_length=name_len,
                    )
                list_pos = host_start + name_len
            return None

        pos += ext_len

    return None


def build_tls_fragments(
    payload: bytes,
    profile: DPIProfile,
    sni_match: SNIMatch | None = None,
) -> list[TCPFragment]:
    """Split a TLS ClientHello payload into DPI-evading TCP segments.

    Preserves exact byte stream integrity while preventing middleboxes from
    reassembling the TLS record header and plaintext SNI field.
    """
    total_len = len(payload)
    if not profile.fragment_tls or total_len <= 4:
        return [TCPFragment(payload=payload, seq_offset=0)]

    cut_points: set[int] = set()
    header_cut = max(1, min(profile.sni_split_offset, total_len - 1))
    cut_points.add(header_cut)

    if profile.split_at_sni and sni_match is not None and sni_match.host_length >= 2:
        delta = min(profile.sni_split_offset, max(1, sni_match.host_length // 2))
        sni_cut = sni_match.host_offset + delta
        if 1 <= sni_cut < total_len:
            cut_points.add(sni_cut)

    ordered_cuts = [0, *sorted(cut_points), total_len]
    real_fragments: list[TCPFragment] = []
    for idx in range(len(ordered_cuts) - 1):
        start = ordered_cuts[idx]
        end = ordered_cuts[idx + 1]
        if end > start:
            real_fragments.append(
                TCPFragment(payload=payload[start:end], seq_offset=start)
            )

    if profile.reverse_fragments and len(real_fragments) > 1:
        real_fragments.reverse()

    fragments: list[TCPFragment] = []
    if profile.fake_packet_ttl > 0:
        # Low-TTL decoy packet carrying harmless zeroed SNI bytes to desync stateful DPI
        decoy_payload = b"\x16\x03\x01\x00\x04\x01\x00\x00\x00"
        fragments.append(
            TCPFragment(
                payload=decoy_payload,
                seq_offset=0,
                is_fake=True,
                ttl=profile.fake_packet_ttl,
            )
        )

    fragments.extend(real_fragments)
    return fragments


def split_tls_client_hello_records(
    payload: bytes,
    profile: DPIProfile,
    sni_match: SNIMatch | None = None,
) -> bytes:
    """Split a TLS ClientHello record into two valid RFC 5246 TLS records across the SNI field.

    Defeats stateful TCP-reassembling DPI appliances (such as Turkcell Superonline)
    that stitch TCP segments together before parsing the first TLS record header.
    """
    if not profile.fragment_tls or len(payload) <= 9:
        return payload
    if payload[0] != 0x16 or payload[1] != 0x03:
        return payload

    record_len = struct.unpack_from("!H", payload, 3)[0]
    if record_len <= 4 or 5 + record_len > len(payload):
        return payload

    record_header_prefix = payload[:3]
    first_record_body = payload[5 : 5 + record_len]
    trailing_bytes = payload[5 + record_len :]

    resolved_sni = sni_match if sni_match is not None else extract_tls_sni(payload)
    if (
        profile.split_at_sni
        and resolved_sni is not None
        and resolved_sni.host_offset >= 5
    ):
        delta = min(profile.sni_split_offset, max(1, resolved_sni.host_length // 2))
        body_cut = (resolved_sni.host_offset - 5) + delta
    else:
        body_cut = profile.sni_split_offset

    body_cut = max(1, min(body_cut, len(first_record_body) - 1))
    part1 = first_record_body[:body_cut]
    part2 = first_record_body[body_cut:]

    rec1 = record_header_prefix + struct.pack("!H", len(part1)) + part1
    rec2 = record_header_prefix + struct.pack("!H", len(part2)) + part2
    return rec1 + rec2 + trailing_bytes


def extract_http_host(payload: bytes) -> str | None:
    """Extract the hostname (without port) from an outbound HTTP request payload."""
    if len(payload) < 16 or not payload.startswith(_HTTP_METHODS):
        return None
    header_end = payload.find(b"\r\n\r\n")
    search_area = payload if header_end == -1 else payload[: header_end + 4]
    match = _HTTP_HOST_RE.search(search_area)
    if not match:
        return None
    raw_value = match.group(2).strip()
    try:
        host_port = raw_value.decode("ascii").strip().lower()
    except UnicodeDecodeError:
        return None
    return host_port.split(":", 1)[0].strip() or None


def build_http_fragments(payload: bytes, profile: DPIProfile) -> list[TCPFragment]:
    """Apply HTTP Host header case-mixing and hostname splitting for port 80 DPI bypass."""
    if (
        not profile.fragment_http
        or len(payload) < 16
        or not payload.startswith(_HTTP_METHODS)
    ):
        return [TCPFragment(payload=payload, seq_offset=0)]

    match = _HTTP_HOST_RE.search(payload)
    if not match:
        return [TCPFragment(payload=payload, seq_offset=0)]

    modified = bytearray(payload)
    if profile.mix_http_host_case:
        # "Host" -> "hoSt" keeps exact 4-byte length so TCP sequence numbers never drift
        key_start, key_end = match.span(1)
        if key_end - key_start == 4:
            modified[key_start:key_end] = b"hoSt"

    val_start, val_end = match.span(2)
    val_len = val_end - val_start
    if val_len >= 2:
        split_pos = val_start + max(1, min(profile.sni_split_offset, val_len // 2))
    else:
        split_pos = max(1, len(modified) // 2)

    immutable_bytes = bytes(modified)
    segments = [
        TCPFragment(payload=immutable_bytes[:split_pos], seq_offset=0),
        TCPFragment(payload=immutable_bytes[split_pos:], seq_offset=split_pos),
    ]
    if profile.reverse_fragments:
        segments.reverse()
    return segments
