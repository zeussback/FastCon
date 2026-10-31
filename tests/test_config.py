"""Unit tests for FastCon configuration, ISP profiles, and domain matching."""

from pathlib import Path

from src.config import (
    DEFAULT_TARGET_DOMAINS,
    ISP_PROFILES,
    AppSettings,
    ScopeMode,
    matches_target_domain,
)


def test_default_domains_include_discord_and_roblox() -> None:
    """Default domain list must cover Discord (web, voice, CDN) and Roblox."""
    required = {
        "discord.com",
        "discordapp.com",
        "discord.gg",
        "discord.media",
        "roblox.com",
        "rbxcdn.com",
    }
    assert required.issubset(set(DEFAULT_TARGET_DOMAINS))


def test_superonline_and_general_profiles_exist() -> None:
    """Both Superonline-specific and General Turkey DPI profiles must be defined."""
    assert "superonline" in ISP_PROFILES
    assert "turkey_general" in ISP_PROFILES

    superonline = ISP_PROFILES["superonline"]
    assert superonline.fragment_tls is True
    assert superonline.reverse_fragments is True
    assert superonline.enable_doh is True
    assert superonline.sni_split_offset >= 1


def test_matches_target_domain_exact_and_subdomains() -> None:
    """Domain matcher matches exact domains and valid subdomains without false positives."""
    targets = ["discord.com", "roblox.com", "rbxcdn.com"]

    assert matches_target_domain("discord.com", targets) is True
    assert matches_target_domain("gateway.discord.com", targets) is True
    assert matches_target_domain("VOICE.DISCORD.COM.", targets) is True
    assert matches_target_domain("tr.rbxcdn.com", targets) is True

    # Must not match partial suffix spoofing or unrelated sites
    assert matches_target_domain("notdiscord.com", targets) is False
    assert matches_target_domain("discord.com.example.org", targets) is False
    assert matches_target_domain("turkcell.com.tr", targets) is False
    assert matches_target_domain("", targets) is False


def test_settings_should_process_domain_respects_scope_mode() -> None:
    """In SELECTED_ONLY mode only listed domains match; in ALL_SITES any valid domain matches."""
    selected_settings = AppSettings(scope_mode=ScopeMode.SELECTED_ONLY)
    assert selected_settings.should_process_domain(" www.roblox.com ") is True
    assert selected_settings.should_process_domain("banka.com.tr") is False
    assert selected_settings.should_process_domain(None) is False

    all_settings = AppSettings(scope_mode=ScopeMode.ALL_SITES)
    assert all_settings.should_process_domain("any-site.org") is True
    assert all_settings.should_process_domain(None) is False


def test_settings_save_and_load_roundtrip(tmp_path: Path) -> None:
    """Settings persist cleanly to JSON and reload with identical values."""
    config_file = tmp_path / "settings.json"
    original = AppSettings(
        profile_id="superonline",
        scope_mode=ScopeMode.SELECTED_ONLY,
        target_domains=["discord.com", "roblox.com", "custom.org"],
        minimize_to_tray=True,
        autostart_enabled=True,
    )
    original.save(config_file)

    loaded = AppSettings.load(config_file)
    assert loaded.profile_id == "superonline"
    assert loaded.scope_mode == ScopeMode.SELECTED_ONLY
    assert "custom.org" in loaded.target_domains
    assert loaded.minimize_to_tray is True
    assert loaded.autostart_enabled is True


def test_settings_load_returns_defaults_on_missing_or_corrupt_file(
    tmp_path: Path,
) -> None:
    """Missing or malformed settings file falls back safely to defaults without crashing."""
    missing_file = tmp_path / "does_not_exist.json"
    default_loaded = AppSettings.load(missing_file)
    assert default_loaded.profile_id == "superonline"

    corrupt_file = tmp_path / "corrupt.json"
    corrupt_file.write_text("{invalid json!", encoding="utf-8")
    recovered = AppSettings.load(corrupt_file)
    assert recovered.profile_id == "superonline"
    assert "discord.com" in recovered.target_domains


def test_autostart_enable_creates_scheduled_task() -> None:
    """Enabling autostart invokes schtasks /Create with ONLOGON and --minimized."""
    from src.autostart import AutoStartManager

    executed_commands: list[list[str]] = []

    def fake_runner(cmd: list[str]) -> tuple[int, str]:
        executed_commands.append(cmd)
        return 0, "SUCCESS"

    manager = AutoStartManager(command_runner=fake_runner)
    ok, msg = manager.set_autostart(True)

    assert ok is True
    assert msg
    assert len(executed_commands) == 1
    cmd = executed_commands[0]
    assert cmd[0] == "schtasks"
    assert "/Create" in cmd
    assert "FastConAutoStart" in cmd
    assert "/SC" in cmd and "ONLOGON" in cmd


def test_autostart_disable_deletes_scheduled_task() -> None:
    """Disabling autostart removes the FastConAutoStart scheduled task."""
    from src.autostart import AutoStartManager

    executed_commands: list[list[str]] = []

    def fake_runner(cmd: list[str]) -> tuple[int, str]:
        executed_commands.append(cmd)
        return 0, "SUCCESS"

    manager = AutoStartManager(command_runner=fake_runner)
    ok, _ = manager.set_autostart(False)

    assert ok is True
    assert len(executed_commands) == 1
    assert "/Delete" in executed_commands[0]
    assert "FastConAutoStart" in executed_commands[0]


def test_autostart_handles_failure_gracefully_and_queries_status() -> None:
    """Querying and failing commands return clean boolean/error messages without raising."""
    from src.autostart import AutoStartManager

    def failing_runner(cmd: list[str]) -> tuple[int, str]:
        return 1, "ERROR: Access is denied."

    manager = AutoStartManager(command_runner=failing_runner)
    assert manager.is_autostart_enabled() is False
    ok, msg = manager.set_autostart(True)
    assert ok is False
    assert "Access is denied" in msg or "Yönetici" in msg
