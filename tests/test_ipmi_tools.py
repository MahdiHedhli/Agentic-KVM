"""Tests for IPMI tool wrappers."""

from __future__ import annotations


class FakeIpmiClient:
    async def power_state(self):
        return {"powerstate": "on"}

    async def set_power(self, state: str, *, wait: bool = False):
        return {"powerstate": state, "wait": wait}

    async def health(self):
        return {"health": {"label": "ok"}, "badreadings": []}

    async def sensors(self):
        return [
            {"name": "CPU Temp", "health": {"label": "ok"}, "unavailable": False},
            {"name": "FAN1", "health": {"label": "warning"}, "unavailable": False},
        ]

    async def event_log(self, *, limit: int = 50):
        return {"events": [{"id": 1}], "count": 1, "truncated_to": limit}

    async def inventory(self):
        return {"items": [{"name": "System"}], "count": 1}

    async def firmware(self):
        return {"firmware": {"BMC": {"version": "1.0"}}}

    async def system_power_watts(self):
        return {"watts": 123}


class TestIpmiTools:
    async def test_readonly_tools(self) -> None:
        from pikvm_mcp.tools import ipmi

        client = FakeIpmiClient()

        assert await ipmi.power_state(client) == {"powerstate": "on"}
        assert await ipmi.health(client) == {"health": {"label": "ok"}, "badreadings": []}
        assert (await ipmi.event_log(client, limit=10))["truncated_to"] == 10
        assert (await ipmi.inventory(client))["count"] == 1
        assert (await ipmi.firmware(client))["firmware"]["BMC"]["version"] == "1.0"
        assert (await ipmi.system_power_watts(client))["watts"] == 123

    async def test_sensors_can_filter_unhealthy(self) -> None:
        from pikvm_mcp.tools import ipmi

        result = await ipmi.sensors(FakeIpmiClient(), only_unhealthy=True)

        assert result["count"] == 1
        assert result["sensors"][0]["name"] == "FAN1"

    async def test_power_tools(self) -> None:
        from pikvm_mcp.tools import ipmi

        client = FakeIpmiClient()

        assert await ipmi.power_on(client, wait=True) == {"powerstate": "on", "wait": True}
        assert await ipmi.power_off(client) == {"powerstate": "off", "wait": False}
        assert await ipmi.power_shutdown(client) == {"powerstate": "shutdown", "wait": False}
        assert await ipmi.power_reset(client) == {"powerstate": "reset", "wait": False}
