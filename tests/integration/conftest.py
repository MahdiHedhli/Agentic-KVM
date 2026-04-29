"""Shared fixtures for live PiKVM integration tests."""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from dotenv import dotenv_values

from pikvm_mcp.client import PiKVMClient
from pikvm_mcp.config import AppConfig
from pikvm_mcp.ipmi_client import IpmiClient


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ENV_KEYS = {
    "PIKVM_TARGETS",
    "PIKVM_DEFAULT_TARGET",
    "PIKVM_AUDIT_DIR",
    "PIKVM_OPERATOR_ID",
    "PIKVM_FULL_CAPTURE",
    "IPMI_TARGETS",
    "IPMI_DEFAULT_TARGET",
}


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def load_live_env() -> dict[str, str]:
    """Load PiKVM env values from .env, then overlay the process env."""
    values = {
        k: v
        for k, v in dotenv_values(REPO_ROOT / ".env").items()
        if k in APP_ENV_KEYS and v is not None
    }
    values.update({k: v for k, v in os.environ.items() if k in APP_ENV_KEYS})
    return values


def require_integration_enabled() -> None:
    if not _enabled("PIKVM_INTEGRATION"):
        pytest.skip("set PIKVM_INTEGRATION=1 to run live PiKVM integration tests")


def require_ipmi_integration_enabled() -> None:
    if not _enabled("IPMI_INTEGRATION"):
        pytest.skip("set IPMI_INTEGRATION=1 to run live IPMI integration tests")


def require_action_enabled(kind: str) -> None:
    var = f"PIKVM_ALLOW_{kind.upper()}_ACTIONS"
    if not _enabled(var):
        pytest.skip(f"set {var}=1 to run live {kind.upper()} action tests")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def live_env() -> dict[str, str]:
    require_integration_enabled()
    return load_live_env()


@pytest.fixture(scope="session")
def live_ipmi_env() -> dict[str, str]:
    require_ipmi_integration_enabled()
    return load_live_env()


@pytest.fixture(scope="session")
def live_config(live_env: dict[str, str]) -> AppConfig:
    config = AppConfig(**live_env)
    target = config.resolve_target()
    try:
        socket.getaddrinfo(target.host, target.port)
    except OSError as exc:
        pytest.skip(f"live PiKVM target is not resolvable: {target.host}: {exc}")
    return config


@pytest.fixture(scope="session")
def live_ipmi_config(live_ipmi_env: dict[str, str]) -> AppConfig:
    config = AppConfig(**live_ipmi_env)
    target = config.resolve_ipmi_target()
    try:
        socket.getaddrinfo(target.host, target.port)
    except OSError as exc:
        pytest.skip(f"live IPMI target is not resolvable: {target.host}: {exc}")
    return config


@pytest.fixture
async def live_client(live_config: AppConfig) -> AsyncIterator[PiKVMClient]:
    client = PiKVMClient(live_config.resolve_target())
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
async def live_ipmi_client(live_ipmi_config: AppConfig) -> AsyncIterator[IpmiClient]:
    client = IpmiClient(live_ipmi_config.resolve_ipmi_target())
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
def hid_actions_enabled() -> None:
    require_action_enabled("hid")


@pytest.fixture
def msd_actions_enabled() -> None:
    require_action_enabled("msd")


@pytest.fixture
def atx_actions_enabled() -> None:
    require_action_enabled("atx")
