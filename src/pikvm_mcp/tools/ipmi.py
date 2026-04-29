"""IPMI/BMC tools.

These tools target standards-based BMC/IPMI functions such as power state,
sensor readings, system event logs, inventory, and firmware.  Supermicro is
the first lab target, but the tool names stay generic because IPMI is not
Supermicro-specific.
"""

from __future__ import annotations

from typing import Any


async def power_state(client: Any) -> dict[str, Any]:
    """Return current system power state."""
    return await client.power_state()


async def power_on(client: Any, *, wait: bool = False) -> dict[str, Any]:
    """Request system power on."""
    return await client.set_power("on", wait=wait)


async def power_off(client: Any, *, wait: bool = False) -> dict[str, Any]:
    """Request immediate system power off."""
    return await client.set_power("off", wait=wait)


async def power_shutdown(client: Any, *, wait: bool = False) -> dict[str, Any]:
    """Request graceful OS shutdown via IPMI, when supported by the host."""
    return await client.set_power("shutdown", wait=wait)


async def power_reset(client: Any, *, wait: bool = False) -> dict[str, Any]:
    """Request immediate system reset."""
    return await client.set_power("reset", wait=wait)


async def health(client: Any) -> dict[str, Any]:
    """Return pyghmi's summarized BMC health state."""
    return await client.health()


async def sensors(client: Any, *, only_unhealthy: bool = False) -> dict[str, Any]:
    """Return normalized sensor readings."""
    readings = await client.sensors()
    if only_unhealthy:
        readings = [
            reading
            for reading in readings
            if reading.get("health", {}).get("label") not in {"ok", "unknown"}
            or reading.get("unavailable")
        ]
    return {"sensors": readings, "count": len(readings), "only_unhealthy": only_unhealthy}


async def event_log(client: Any, *, limit: int = 50) -> dict[str, Any]:
    """Return the BMC system event log without clearing it."""
    return await client.event_log(limit=limit)


async def inventory(client: Any) -> dict[str, Any]:
    """Return BMC-provided inventory data."""
    return await client.inventory()


async def firmware(client: Any) -> dict[str, Any]:
    """Return BMC-provided firmware data."""
    return await client.firmware()


async def system_power_watts(client: Any) -> dict[str, Any]:
    """Return system power draw in watts when the BMC supports it."""
    return await client.system_power_watts()
