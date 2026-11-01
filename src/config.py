"""Configuration, DPI bypass profiles, and domain filtering for FastCon."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.json"

DEFAULT_TARGET_DOMAINS: tuple[str, ...] = (
    # Discord desktop, web, gateway, voice, and media CDNs
    "discord.com",
    "discordapp.com",
    "discordapp.net",
    "discord.gg",
    "discord.media",
    "discordcdn.com",
    "discord.co",
    "discordstatus.com",
    # Roblox web, desktop player, APIs, and asset CDNs
    "roblox.com",
    "rbxcdn.com",
    "robloxlabs.com",
    "rbx.com",
    "rbxinfra.net",
)

DEFAULT_DOH_ENDPOINTS: tuple[str, ...] = (
    "https://1.1.1.1/dns-query",
    "https://8.8.8.8/dns-query",
    "https://1.0.0.1/dns-query",
    "https://8.8.4.4/dns-query",
)

DOH_PROVIDER_PRESETS: dict[str, tuple[str, ...]] = {
    "Otomatik Yedekli (Cloudflare + Google)": DEFAULT_DOH_ENDPOINTS,
    "Cloudflare DoH (1.1.1.1)": (
        "https://1.1.1.1/dns-query",
        "https://1.0.0.1/dns-query",
    ),
    "Google DoH (8.8.8.8)": (
        "https://8.8.8.8/dns-query",
        "https://8.8.4.4/dns-query",
    ),
    "Quad9 DoH (9.9.9.9)": (
        "https://9.9.9.9/dns-query",
        "https://149.112.112.112/dns-query",
    ),
}


class ScopeMode(StrEnum):
    """Determines whether DPI bypass applies only to target domains or all traffic."""

    SELECTED_ONLY = "selected_only"
    ALL_SITES = "all_sites"


@dataclass(frozen=True)
class DPIProfile:
    """Parameters controlling TCP/TLS fragmentation and DNS handling."""

    profile_id: str
    display_name: str
    description: str
    fragment_tls: bool = True
    fragment_http: bool = True
    sni_split_offset: int = 2
    split_at_sni: bool = True
    reverse_fragments: bool = True
    mix_http_host_case: bool = True
    fake_packet_ttl: int = 0
    enable_doh: bool = True


_CORE_PROFILES: dict[str, DPIProfile] = {
    "standard": DPIProfile(
        profile_id="standard",
        display_name="Standart Mod (Önerilen)",
        description="TLS kayıt bölme ve şifreli DoH ile dengeli, sıfır gecikmeli bağlantı.",
        fragment_tls=True,
        fragment_http=True,
        sni_split_offset=2,
        split_at_sni=True,
        reverse_fragments=True,
        mix_http_host_case=True,
        fake_packet_ttl=0,
        enable_doh=True,
    ),
    "deep": DPIProfile(
        profile_id="deep",
        display_name="Derin Paket Aşma (Agresif)",
        description="Katı ağ filtreleri için çoklu SNI bölme ve düşük TTL desenkronizasyonu.",
        fragment_tls=True,
        fragment_http=True,
        sni_split_offset=2,
        split_at_sni=True,
        reverse_fragments=True,
        mix_http_host_case=True,
        fake_packet_ttl=3,
        enable_doh=True,
    ),
    "light": DPIProfile(
        profile_id="light",
        display_name="Hafif Mod (Temel)",
        description="Yalnızca temel el sıkışma başlık bölme ve şifreli DNS çözümleme.",
        fragment_tls=True,
        fragment_http=True,
        sni_split_offset=2,
        split_at_sni=False,
        reverse_fragments=True,
        mix_http_host_case=True,
        fake_packet_ttl=0,
        enable_doh=True,
    ),
}

_LEGACY_ALIASES: dict[str, str] = {
    "superonline": "standard",
    "superonline_ttl": "deep",
    "turkey_general": "light",
}


class ProfileRegistry(Mapping[str, DPIProfile]):
    """Profile dictionary supporting both neutral IDs and legacy profile aliases."""

    def __getitem__(self, key: str) -> DPIProfile:
        resolved = _LEGACY_ALIASES.get(key, key)
        return _CORE_PROFILES[resolved]

    def __iter__(self) -> Iterator[str]:
        return iter(_CORE_PROFILES)

    def __len__(self) -> int:
        return len(_CORE_PROFILES)


ISP_PROFILES: ProfileRegistry = ProfileRegistry()


def resolve_profile_id(profile_id: str) -> str:
    """Normalize a profile identifier, mapping legacy IDs to current canonical IDs."""
    mapped = _LEGACY_ALIASES.get(profile_id, profile_id)
    return mapped if mapped in _CORE_PROFILES else "standard"


def normalize_domain(domain: str) -> str:
    """Normalize a hostname for reliable suffix comparison."""
    return domain.strip().lower().rstrip(".")


def matches_target_domain(hostname: str | None, target_domains: Iterable[str]) -> bool:
    """Return True if hostname equals or is a subdomain of any domain in target_domains."""
    if not hostname:
        return False
    normalized_host = normalize_domain(hostname)
    if not normalized_host:
        return False

    for raw_target in target_domains:
        target = normalize_domain(raw_target)
        if not target:
            continue
        if normalized_host == target or normalized_host.endswith(f".{target}"):
            return True
    return False


@dataclass
class AppSettings:
    """User-configurable runtime and UI preferences persisted to JSON."""

    profile_id: str = "standard"
    scope_mode: ScopeMode = ScopeMode.SELECTED_ONLY
    target_domains: list[str] = field(
        default_factory=lambda: list(DEFAULT_TARGET_DOMAINS)
    )
    doh_endpoints: list[str] = field(
        default_factory=lambda: list(DEFAULT_DOH_ENDPOINTS)
    )
    minimize_to_tray: bool = True
    autostart_enabled: bool = False

    @property
    def active_profile(self) -> DPIProfile:
        """Return the active DPIProfile, falling back to 'standard' if invalid."""
        canonical = resolve_profile_id(self.profile_id)
        return _CORE_PROFILES[canonical]

    def should_process_domain(self, hostname: str | None) -> bool:
        """Check whether a packet for the given hostname should undergo DPI/DoH bypass."""
        if not hostname or not normalize_domain(hostname):
            return False
        if self.scope_mode == ScopeMode.ALL_SITES:
            return True
        return matches_target_domain(hostname, self.target_domains)

    def save(self, path: Path = DEFAULT_SETTINGS_PATH) -> None:
        """Persist settings atomically as formatted UTF-8 JSON."""
        payload = {
            "profile_id": resolve_profile_id(self.profile_id),
            "scope_mode": self.scope_mode.value,
            "target_domains": [
                normalize_domain(d) for d in self.target_domains if normalize_domain(d)
            ],
            "doh_endpoints": list(self.doh_endpoints),
            "minimize_to_tray": self.minimize_to_tray,
            "autostart_enabled": self.autostart_enabled,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path = DEFAULT_SETTINGS_PATH) -> AppSettings:
        """Load settings from JSON, safely returning defaults on missing or invalid data."""
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return cls()

            profile_id = resolve_profile_id(str(raw.get("profile_id", "standard")))

            raw_scope = str(raw.get("scope_mode", ScopeMode.SELECTED_ONLY.value))
            try:
                scope_mode = ScopeMode(raw_scope)
            except ValueError:
                scope_mode = ScopeMode.SELECTED_ONLY

            raw_domains = raw.get("target_domains")
            if isinstance(raw_domains, list) and raw_domains:
                domains = [
                    normalize_domain(str(d))
                    for d in raw_domains
                    if normalize_domain(str(d))
                ]
            else:
                domains = list(DEFAULT_TARGET_DOMAINS)

            raw_doh = raw.get("doh_endpoints")
            if isinstance(raw_doh, list) and raw_doh:
                doh_endpoints = [str(u) for u in raw_doh if str(u).strip()]
            else:
                doh_endpoints = list(DEFAULT_DOH_ENDPOINTS)

            return cls(
                profile_id=profile_id,
                scope_mode=scope_mode,
                target_domains=domains,
                doh_endpoints=doh_endpoints,
                minimize_to_tray=bool(raw.get("minimize_to_tray", True)),
                autostart_enabled=bool(raw.get("autostart_enabled", False)),
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return cls()
