"""Tests for ATX power control tools."""

from __future__ import annotations

import pytest
import respx

from pikvm_mcp.client import PiKVMClient
from pikvm_mcp.config import TargetConfig
from pikvm_mcp.tools.atx import atx_power_on, atx_reset, atx_state

BASE = "https://pikvm-test.ts.net:443"


@pytest.fixture
def cfg() -> TargetConfig:
    return TargetConfig(
        name="test-kvm",
        host="pikvm-test.ts.net",
        port=443,
        https=True,
        username="admin",
        password="secret",  # type: ignore[arg-type]
        verify_ssl=False,
    )


class TestATXTools:
    async def test_atx_state(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            respx.get(f"{BASE}/api/atx").respond(
                json={
                    "ok": True,
                    "result": {
                        "enabled": True,
                        "busy": False,
                        "leds": {"power": True, "hdd": False},
                    },
                }
            )
            result = await atx_state(client)
        assert result["ok"] is True
        assert result["result"]["leds"]["power"] is True
        await client.close()

    async def test_atx_power_on(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            route = respx.post(f"{BASE}/api/atx/power").respond(json={"ok": True})
            result = await atx_power_on(client)
            assert result["ok"] is True
            assert "action=on" in str(route.calls[0].request.url)
        await client.close()

    async def test_atx_reset(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            route = respx.post(f"{BASE}/api/atx/power").respond(json={"ok": True})
            result = await atx_reset(client)
            assert result["ok"] is True
            assert "action=reset" in str(route.calls[0].request.url)
        await client.close()
