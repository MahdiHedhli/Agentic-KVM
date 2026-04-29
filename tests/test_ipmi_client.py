"""Tests for IPMI client normalization and pyghmi wrapper behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pyghmi.constants import Health

from pikvm_mcp.config import IpmiTargetConfig
from pikvm_mcp.ipmi_client import IpmiClient, _health_label, _jsonable


@dataclass
class FakeSensor:
    name: str
    type: str = "Temperature"
    value: float | None = 42.0
    units: str = "C"
    imprecision: float | None = 0.5
    states: list[str] | None = None
    state_ids: list[str] | None = None
    unavailable: bool = False
    health: int = Health.Ok

    def __post_init__(self) -> None:
        if self.states is None:
            self.states = []
        if self.state_ids is None:
            self.state_ids = []

    def simplestring(self) -> str:
        return f"{self.name}: {self.value}{self.units}"


class FakeCommand:
    instances: list["FakeCommand"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.set_power_calls: list[tuple[str, bool]] = []
        FakeCommand.instances.append(self)

    def get_power(self) -> dict[str, str]:
        return {"powerstate": "on"}

    def set_power(self, state: str, wait: bool = False) -> dict[str, str]:
        self.set_power_calls.append((state, wait))
        return {"powerstate": state}

    def get_health(self) -> dict[str, Any]:
        return {
            "health": Health.Warning,
            "badreadings": [FakeSensor("FAN1", value=0, units="RPM", health=Health.Warning)],
        }

    def get_sensor_data(self):
        yield FakeSensor("CPU Temp")
        yield FakeSensor("FAN1", value=0, units="RPM", health=Health.Warning)

    def get_event_log(self, clear: bool = False):
        assert clear is False
        yield {"record_id": 1, "event": "Power Unit", "severity": "ok"}
        yield {"record_id": 2, "event": "Fan low", "severity": "warning"}

    def get_inventory(self):
        yield "System", {"serial": "ABC123", "manufacturer": "Supermicro"}

    def get_firmware(self):
        return {"BMC": {"version": "1.0"}}

    def get_system_power_watts(self):
        return 123


def _cfg() -> IpmiTargetConfig:
    return IpmiTargetConfig(
        name="sm-lab",
        host="ipmi-lab.ts.net",
        username="operator",
        password="secret",
        kg="kg-secret",
        privlevel="ADMINISTRATOR",
    )


class TestHealthLabel:
    def test_health_labels(self) -> None:
        assert _health_label(Health.Ok) == "ok"
        assert _health_label(Health.Warning) == "warning"
        assert _health_label(Health.Critical) == "critical"
        assert _health_label(Health.Failed) == "failed"


class TestJsonable:
    def test_sensor_reading_normalization(self) -> None:
        out = _jsonable(FakeSensor("CPU Temp"))
        assert out["name"] == "CPU Temp"
        assert out["health"] == {"code": 0, "label": "ok"}
        assert out["summary"] == "CPU Temp: 42.0C"


class TestIpmiClient:
    async def test_connects_with_expected_kwargs(self) -> None:
        FakeCommand.instances.clear()
        client = IpmiClient(_cfg(), command_factory=FakeCommand)

        result = await client.power_state()

        assert result == {"powerstate": "on"}
        command = FakeCommand.instances[0]
        assert command.kwargs["bmc"] == "ipmi-lab.ts.net"
        assert command.kwargs["userid"] == "operator"
        assert command.kwargs["password"] == "secret"
        assert command.kwargs["port"] == 623
        assert command.kwargs["kg"] == "kg-secret"
        assert command.kwargs["privlevel"] == "ADMINISTRATOR"

    async def test_power_action(self) -> None:
        FakeCommand.instances.clear()
        client = IpmiClient(_cfg(), command_factory=FakeCommand)

        result = await client.set_power("reset", wait=True)

        assert result == {"powerstate": "reset"}
        assert FakeCommand.instances[0].set_power_calls == [("reset", True)]

    async def test_health_normalized(self) -> None:
        client = IpmiClient(_cfg(), command_factory=FakeCommand)

        result = await client.health()

        assert result["health"] == {"code": 1, "label": "warning"}
        assert result["badreadings"][0]["name"] == "FAN1"

    async def test_sensors_normalized(self) -> None:
        client = IpmiClient(_cfg(), command_factory=FakeCommand)

        result = await client.sensors()

        assert [sensor["name"] for sensor in result] == ["CPU Temp", "FAN1"]
        assert result[1]["health"]["label"] == "warning"

    async def test_event_log_limited(self) -> None:
        client = IpmiClient(_cfg(), command_factory=FakeCommand)

        result = await client.event_log(limit=1)

        assert result["count"] == 1
        assert result["total_count"] == 2
        assert result["events"][0]["record_id"] == 2
        assert result["truncated_to"] == 1

    async def test_inventory_firmware_and_power(self) -> None:
        client = IpmiClient(_cfg(), command_factory=FakeCommand)

        inventory = await client.inventory()
        firmware = await client.firmware()
        watts = await client.system_power_watts()

        assert inventory["items"][0]["name"] == "System"
        assert firmware["firmware"]["BMC"]["version"] == "1.0"
        assert watts["watts"] == 123
