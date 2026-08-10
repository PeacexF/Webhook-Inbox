from pathlib import Path

import pytest

from app.config import Settings, load_settings


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("MONGO_DATABASE", "LOG_LEVEL", "LIMITS__MAX_PAYLOAD_SIZE"):
        monkeypatch.delenv(key, raising=False)


def test_defaults() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.mongo_database == "webhook_inbox"
    assert settings.limits.max_payload_size == 10 * 1024 * 1024
    assert settings.replay.allow_private_networks is False


def test_yaml_overrides_defaults(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("limits:\n  max_payload_size: 2048\nretention:\n  default_days: 7\n")

    settings = load_settings(config)
    assert settings.limits.max_payload_size == 2048
    assert settings.retention.default_days == 7


def test_env_beats_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("limits:\n  max_payload_size: 2048\n")
    monkeypatch.setenv("LIMITS__MAX_PAYLOAD_SIZE", "4096")

    assert load_settings(config).limits.max_payload_size == 4096


def test_missing_yaml_falls_back_to_defaults(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "absent.yaml")
    assert settings.limits.max_payload_size == 10 * 1024 * 1024
