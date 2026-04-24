"""Tests for multi-target configuration."""

from __future__ import annotations

import json

import pytest

from pikvm_mcp.config import AppConfig, TargetConfig, load_env_file_from_environment


class TestTargetConfig:
    def test_base_url_https(self) -> None:
        cfg = TargetConfig(name="test", host="pikvm.ts.net")
        assert cfg.base_url == "https://pikvm.ts.net:443"

    def test_base_url_http(self) -> None:
        cfg = TargetConfig(name="test", host="pikvm.ts.net", https=False, port=80)
        assert cfg.base_url == "http://pikvm.ts.net:80"

    def test_defaults(self) -> None:
        cfg = TargetConfig(name="test", host="pikvm.ts.net")
        assert cfg.username == "admin"
        assert cfg.verify_ssl is False
        assert cfg.cert_fingerprint is None


class TestAppConfig:
    def test_parse_targets_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        targets = [
            {"name": "lab", "host": "pikvm-lab.ts.net"},
            {"name": "prod", "host": "pikvm-prod.ts.net", "port": 8443},
        ]
        monkeypatch.setenv("PIKVM_TARGETS", json.dumps(targets))
        cfg = AppConfig()
        assert len(cfg.targets) == 2
        assert cfg.targets[0].name == "lab"
        assert cfg.targets[1].port == 8443

    def test_resolve_target_by_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        targets = [
            {"name": "a", "host": "a.ts.net"},
            {"name": "b", "host": "b.ts.net"},
        ]
        monkeypatch.setenv("PIKVM_TARGETS", json.dumps(targets))
        cfg = AppConfig()
        assert cfg.resolve_target("b").host == "b.ts.net"

    def test_resolve_target_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        targets = [
            {"name": "a", "host": "a.ts.net"},
            {"name": "b", "host": "b.ts.net"},
        ]
        monkeypatch.setenv("PIKVM_TARGETS", json.dumps(targets))
        monkeypatch.setenv("PIKVM_DEFAULT_TARGET", "b")
        cfg = AppConfig()
        assert cfg.resolve_target().name == "b"

    def test_resolve_target_first_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        targets = [{"name": "only", "host": "only.ts.net"}]
        monkeypatch.setenv("PIKVM_TARGETS", json.dumps(targets))
        cfg = AppConfig()
        assert cfg.resolve_target().name == "only"

    def test_resolve_target_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        targets = [{"name": "a", "host": "a.ts.net"}]
        monkeypatch.setenv("PIKVM_TARGETS", json.dumps(targets))
        cfg = AppConfig()
        with pytest.raises(ValueError, match="not found"):
            cfg.resolve_target("nonexistent")

    def test_no_targets_raises(self) -> None:
        cfg = AppConfig()
        with pytest.raises(ValueError, match="No PiKVM targets configured"):
            cfg.resolve_target()

    def test_full_capture_default_false(self) -> None:
        cfg = AppConfig()
        assert cfg.full_capture is False

    def test_full_capture_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIKVM_FULL_CAPTURE", "true")
        cfg = AppConfig()
        assert cfg.full_capture is True


class TestEnvFileLoading:
    def test_no_env_file_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PIKVM_ENV_FILE", raising=False)
        assert load_env_file_from_environment() is None

    def test_loads_config_from_env_file(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "PIKVM_TARGETS='[{\"name\":\"lab\",\"host\":\"lab.ts.net\"}]'\n"
            "PIKVM_DEFAULT_TARGET=lab\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("PIKVM_ENV_FILE", str(env_file))
        monkeypatch.delenv("PIKVM_TARGETS", raising=False)
        monkeypatch.delenv("PIKVM_DEFAULT_TARGET", raising=False)

        loaded = load_env_file_from_environment()
        cfg = AppConfig()

        assert loaded == env_file
        assert cfg.resolve_target().host == "lab.ts.net"

    def test_process_env_wins_over_env_file(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "PIKVM_TARGETS='[{\"name\":\"file\",\"host\":\"file.ts.net\"}]'\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("PIKVM_ENV_FILE", str(env_file))
        monkeypatch.setenv(
            "PIKVM_TARGETS",
            json.dumps([{"name": "process", "host": "process.ts.net"}]),
        )
        monkeypatch.setenv("PIKVM_DEFAULT_TARGET", "process")

        load_env_file_from_environment()
        cfg = AppConfig()

        assert cfg.resolve_target().host == "process.ts.net"
