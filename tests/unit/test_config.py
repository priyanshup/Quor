"""Unit tests for quor/config/: Pydantic models and the user config loader.

Covers the tee mechanism's config surface (ADR-023): FilterConfig.tee,
QuorUserConfig.tee_enabled, and the QUOR_TEE_ENABLED env override.
"""

from __future__ import annotations

import pytest

from quor.config.loader import load_user_config
from quor.config.model import FilterConfig, QuorUserConfig

# ---------------------------------------------------------------------------
# FilterConfig.tee
# ---------------------------------------------------------------------------


class TestFilterConfigTee:
    def test_defaults_to_true(self) -> None:
        fc = FilterConfig(name="x", match_command="^x$")
        assert fc.tee is True

    def test_can_be_disabled(self) -> None:
        fc = FilterConfig(name="x", match_command="^x$", tee=False)
        assert fc.tee is False

    def test_backward_compatible_with_toml_missing_tee_key(self) -> None:
        """Existing filter TOML files (written before ADR-023 shipped) have no
        `tee` key at all — they must still validate, defaulting to enabled."""
        fc = FilterConfig.model_validate({"name": "x", "match_command": "^x$"})
        assert fc.tee is True


# ---------------------------------------------------------------------------
# QuorUserConfig.tee_enabled
# ---------------------------------------------------------------------------


class TestQuorUserConfigTeeEnabled:
    def test_defaults_to_true(self) -> None:
        assert QuorUserConfig().tee_enabled is True

    def test_can_be_disabled(self) -> None:
        assert QuorUserConfig(tee_enabled=False).tee_enabled is False

    def test_backward_compatible_with_config_missing_tee_enabled_key(self) -> None:
        config = QuorUserConfig.model_validate({"mode": "audit"})
        assert config.tee_enabled is True


# ---------------------------------------------------------------------------
# load_user_config() — QUOR_TEE_ENABLED env override
# ---------------------------------------------------------------------------


class TestLoadUserConfigTeeEnvOverride:
    def test_no_env_var_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("QUOR_TEE_ENABLED", raising=False)
        assert load_user_config().tee_enabled is True

    def test_env_var_zero_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QUOR_TEE_ENABLED", "0")
        assert load_user_config().tee_enabled is False

    def test_env_var_false_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QUOR_TEE_ENABLED", "false")
        assert load_user_config().tee_enabled is False

    def test_env_var_one_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QUOR_TEE_ENABLED", "1")
        assert load_user_config().tee_enabled is True

    def test_env_var_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QUOR_TEE_ENABLED", "FALSE")
        assert load_user_config().tee_enabled is False

    def test_invalid_env_var_value_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QUOR_TEE_ENABLED", "maybe")
        assert load_user_config().tee_enabled is True
