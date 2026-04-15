"""Shared fixtures for PiKVM MCP tests.

Uses ``respx`` to mock the PiKVM HTTP API so tests run without a real device.
"""

from __future__ import annotations

import pytest
import respx

from pikvm_mcp.client import PiKVMClient
from pikvm_mcp.config import TargetConfig


@pytest.fixture
def target_config() -> TargetConfig:
    """A minimal target config pointing at a fake host."""
    return TargetConfig(
        name="test-kvm",
        host="pikvm-test.ts.net",
        port=443,
        https=True,
        username="admin",
        password="testpass",  # type: ignore[arg-type]
        verify_ssl=False,
    )


@pytest.fixture
def pikvm_client(target_config: TargetConfig) -> PiKVMClient:
    """A PiKVMClient wired to the test config (use with respx mocking)."""
    return PiKVMClient(target_config)


@pytest.fixture
def mock_pikvm() -> respx.MockRouter:
    """Activate respx mocking for the duration of a test.

    Usage::

        async def test_something(mock_pikvm, pikvm_client):
            mock_pikvm.get("https://pikvm-test.ts.net:443/api/atx").respond(
                json={"ok": True, "result": {"leds": {"power": True}}}
            )
            result = await pikvm_client.get("/api/atx")
            assert result["ok"] is True
    """
    with respx.mock(assert_all_called=False) as router:
        yield router
