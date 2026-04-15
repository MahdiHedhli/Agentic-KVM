"""ATX power control tools.

PiKVM ATX board can pulse the power and reset buttons on the target
machine's motherboard — exactly like pressing them physically.

PiKVM API endpoints:
    GET  /api/atx          — current ATX state (power LED, HDD LED)
    POST /api/atx/power?action=on            — short press power button
    POST /api/atx/power?action=off           — long press power button (force off)
    POST /api/atx/power?action=off_hard      — 5s hold (hard power off)
    POST /api/atx/power?action=reset         — pulse reset button
"""

from __future__ import annotations

from typing import Any


async def atx_state(client: Any) -> dict[str, Any]:
    """Get current ATX state: power LED on/off, HDD LED activity."""
    return await client.get("/api/atx")


async def atx_power_on(client: Any) -> dict[str, Any]:
    """Short-press the power button (turn on if off)."""
    return await client.post("/api/atx/power", params={"action": "on"})


async def atx_power_off(client: Any) -> dict[str, Any]:
    """Long-press the power button (graceful shutdown)."""
    return await client.post("/api/atx/power", params={"action": "off"})


async def atx_power_off_hard(client: Any) -> dict[str, Any]:
    """5-second hold on the power button (force power off)."""
    return await client.post("/api/atx/power", params={"action": "off_hard"})


async def atx_reset(client: Any) -> dict[str, Any]:
    """Pulse the reset button."""
    return await client.post("/api/atx/power", params={"action": "reset"})
