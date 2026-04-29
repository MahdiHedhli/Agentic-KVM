"""Async wrapper around pyghmi for IPMI/BMC targets.

IPMI complements PiKVM.  It provides BMC-level power, health, sensors,
inventory, and event logs, but not PiKVM's video/HID/MSD control plane.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from datetime import date, datetime
from typing import Any

import structlog
from pyghmi.constants import Health
from pyghmi.ipmi.command import Command

from pikvm_mcp.config import IpmiTargetConfig

logger = structlog.get_logger()

CommandFactory = Callable[..., Any]


def _health_label(value: Any) -> str:
    """Return a stable label for pyghmi health bitfields."""
    try:
        code = int(value)
    except (TypeError, ValueError):
        return "unknown"
    if code & int(Health.Failed):
        return "failed"
    if code & int(Health.Critical):
        return "critical"
    if code & int(Health.Warning):
        return "warning"
    if code == int(Health.Ok):
        return "ok"
    return "unknown"


def _jsonable(value: Any) -> Any:
    """Convert pyghmi return objects into JSON-serializable structures."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "simplestring") and callable(value.simplestring):
        return _sensor_reading_to_dict(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, Iterable):
        return [_jsonable(v) for v in value]
    return repr(value)


def _sensor_reading_to_dict(reading: Any) -> dict[str, Any]:
    """Normalize a pyghmi SensorReading object."""
    health_code = int(getattr(reading, "health", 0) or 0)
    return {
        "name": getattr(reading, "name", ""),
        "type": getattr(reading, "type", ""),
        "value": _jsonable(getattr(reading, "value", None)),
        "units": getattr(reading, "units", ""),
        "imprecision": _jsonable(getattr(reading, "imprecision", None)),
        "states": _jsonable(getattr(reading, "states", [])),
        "state_ids": _jsonable(getattr(reading, "state_ids", [])),
        "unavailable": bool(getattr(reading, "unavailable", False)),
        "health": {"code": health_code, "label": _health_label(health_code)},
        "summary": reading.simplestring() if hasattr(reading, "simplestring") else repr(reading),
    }


class IpmiClient:
    """Async facade for one IPMI/BMC target."""

    def __init__(
        self,
        cfg: IpmiTargetConfig,
        *,
        command_factory: CommandFactory = Command,
    ) -> None:
        self._cfg = cfg
        self._command_factory = command_factory
        self._command: Any | None = None
        self._lock = asyncio.Lock()

    @property
    def target_name(self) -> str:
        return self._cfg.name

    def _connect_sync(self) -> Any:
        kwargs: dict[str, Any] = {
            "bmc": self._cfg.host,
            "userid": self._cfg.username,
            "password": self._cfg.password.get_secret_value(),
            "port": self._cfg.port,
            "keepalive": True,
        }
        if self._cfg.kg is not None:
            kwargs["kg"] = self._cfg.kg.get_secret_value()
        if self._cfg.privlevel is not None:
            kwargs["privlevel"] = self._cfg.privlevel
        return self._command_factory(**kwargs)

    async def _ensure_command(self) -> Any:
        if self._command is None:
            self._command = await asyncio.to_thread(self._connect_sync)
        return self._command

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        async with self._lock:
            command = await self._ensure_command()
            try:
                return await asyncio.to_thread(getattr(command, method), *args, **kwargs)
            except Exception as exc:
                logger.warning(
                    "ipmi_command_failed",
                    target=self._cfg.name,
                    command=method,
                    error=str(exc),
                )
                raise ConnectionError(f"IPMI command failed for {self._cfg.name}: {method}") from exc

    async def _call_list(self, method: str, *args: Any, **kwargs: Any) -> list[Any]:
        async with self._lock:
            command = await self._ensure_command()
            try:
                return await asyncio.to_thread(lambda: list(getattr(command, method)(*args, **kwargs)))
            except Exception as exc:
                logger.warning(
                    "ipmi_command_failed",
                    target=self._cfg.name,
                    command=method,
                    error=str(exc),
                )
                raise ConnectionError(f"IPMI command failed for {self._cfg.name}: {method}") from exc

    async def power_state(self) -> dict[str, Any]:
        return _jsonable(await self._call("get_power"))

    async def set_power(self, state: str, *, wait: bool = False) -> dict[str, Any]:
        result = await self._call("set_power", state, wait=wait)
        return _jsonable(result)

    async def health(self) -> dict[str, Any]:
        result = _jsonable(await self._call("get_health"))
        code = int(result.get("health", 0) or 0)
        result["health"] = {"code": code, "label": _health_label(code)}
        result["badreadings"] = [_jsonable(reading) for reading in result.get("badreadings", [])]
        return result

    async def sensors(self) -> list[dict[str, Any]]:
        return [_sensor_reading_to_dict(reading) for reading in await self._call_list("get_sensor_data")]

    async def event_log(self, *, limit: int = 50) -> dict[str, Any]:
        events = [_jsonable(event) for event in await self._call_list("get_event_log", clear=False)]
        total_count = len(events)
        if limit > 0:
            events = events[-limit:]
        return {
            "events": events,
            "count": len(events),
            "total_count": total_count,
            "truncated_to": limit if limit > 0 else None,
        }

    async def inventory(self) -> dict[str, Any]:
        items = []
        for name, data in await self._call_list("get_inventory"):
            items.append({"name": name, "data": _jsonable(data)})
        return {"items": items, "count": len(items)}

    async def firmware(self) -> dict[str, Any]:
        return {"firmware": _jsonable(await self._call("get_firmware"))}

    async def system_power_watts(self) -> dict[str, Any]:
        return {"watts": _jsonable(await self._call("get_system_power_watts"))}

    async def close(self) -> None:
        command = self._command
        self._command = None
        session = getattr(command, "ipmi_session", None)
        logout = getattr(session, "logout", None)
        if callable(logout):
            await asyncio.to_thread(logout)


class IpmiClientRegistry:
    """Manages IpmiClient instances keyed by target name."""

    def __init__(self) -> None:
        self._clients: dict[str, IpmiClient] = {}

    def get_or_create(self, cfg: IpmiTargetConfig) -> IpmiClient:
        if cfg.name not in self._clients:
            self._clients[cfg.name] = IpmiClient(cfg)
        return self._clients[cfg.name]

    async def close_all(self) -> None:
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
