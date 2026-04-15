"""FastMCP server — tool registration and lifecycle.

Runs over stdio so it can be invoked via ``docker exec``, matching the
unifi-network-mcp deployment pattern.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastmcp import FastMCP

from pikvm_mcp.audit import SessionRecorder, audited
from pikvm_mcp.client import ClientRegistry
from pikvm_mcp.config import AppConfig
from pikvm_mcp.tools import atx, hid, msd

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(structlog.get_config()["wrapper_class"]),
    logger_factory=structlog.WriteLoggerFactory(file=sys.stderr),
)
logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# App state (populated in lifespan)
# ---------------------------------------------------------------------------

_config: AppConfig | None = None
_registry: ClientRegistry | None = None
_recorder: SessionRecorder | None = None


def _get_config() -> AppConfig:
    assert _config is not None, "Server not started — config not loaded"
    return _config


def _get_registry() -> ClientRegistry:
    assert _registry is not None, "Server not started — registry not initialized"
    return _registry


def _get_recorder() -> SessionRecorder:
    assert _recorder is not None, "Server not started — recorder not initialized"
    return _recorder


def _resolve_target_name(**kwargs: Any) -> str:
    cfg = _get_config()
    target_name = kwargs.get("target")
    return cfg.resolve_target(target_name).name


# ---------------------------------------------------------------------------
# FastMCP app
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastMCP):
    global _config, _registry, _recorder
    _config = AppConfig()
    _registry = ClientRegistry()
    _recorder = SessionRecorder(
        audit_dir=_config.audit_dir,
        operator_id=_config.operator_id,
    )
    logger.info(
        "server_started",
        targets=[t.name for t in _config.targets],
        default_target=_config.default_target,
        audit_dir=str(_config.audit_dir),
    )
    yield
    _recorder.close()
    await _registry.close_all()
    logger.info("server_stopped")


mcp = FastMCP(
    "Agentic-KVM",
    description="Bare-metal machine control via PiKVM — MSD, ATX, HID",
    lifespan=lifespan,
)


def _client_for(target: str | None = None):
    """Resolve target name → PiKVMClient."""
    cfg = _get_config()
    target_cfg = cfg.resolve_target(target)
    return _get_registry().get_or_create(target_cfg)


# ---------------------------------------------------------------------------
# MSD tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def pikvm_msd_state(target: str | None = None) -> dict[str, Any]:
    """Get MSD state: mounted image, connected status, available storage."""
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(msd.msd_state)
    return await fn(client=_client_for(target), target=target)


@mcp.tool()
async def pikvm_msd_upload_url(
    url: str, target: str | None = None
) -> dict[str, Any]:
    """Download an image from a URL into PiKVM MSD storage (server-side fetch).

    Use this to load ISO installers, firmware images, etc. Long-running — PiKVM
    downloads the file, not the MCP client.
    """
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(msd.msd_upload_url)
    return await fn(client=_client_for(target), url=url, target=target)


@mcp.tool()
async def pikvm_msd_set_image(
    image: str, cdrom: bool = True, target: str | None = None
) -> dict[str, Any]:
    """Select a stored image and set presentation mode (CD-ROM or flash drive)."""
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(msd.msd_set_image)
    return await fn(client=_client_for(target), image=image, cdrom=cdrom, target=target)


@mcp.tool()
async def pikvm_msd_connect(target: str | None = None) -> dict[str, Any]:
    """Plug the virtual drive into the target machine."""
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(msd.msd_connect)
    return await fn(client=_client_for(target), target=target)


@mcp.tool()
async def pikvm_msd_disconnect(target: str | None = None) -> dict[str, Any]:
    """Unplug the virtual drive from the target machine."""
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(msd.msd_disconnect)
    return await fn(client=_client_for(target), target=target)


# ---------------------------------------------------------------------------
# ATX tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def pikvm_atx_state(target: str | None = None) -> dict[str, Any]:
    """Get ATX state: power LED, HDD LED."""
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(atx.atx_state)
    return await fn(client=_client_for(target), target=target)


@mcp.tool()
async def pikvm_atx_power_on(target: str | None = None) -> dict[str, Any]:
    """Short-press power button to turn the machine on."""
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(atx.atx_power_on)
    return await fn(client=_client_for(target), target=target)


@mcp.tool()
async def pikvm_atx_power_off(target: str | None = None) -> dict[str, Any]:
    """Long-press power button for graceful shutdown."""
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(atx.atx_power_off)
    return await fn(client=_client_for(target), target=target)


@mcp.tool()
async def pikvm_atx_power_off_hard(target: str | None = None) -> dict[str, Any]:
    """5-second power button hold for hard power off."""
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(atx.atx_power_off_hard)
    return await fn(client=_client_for(target), target=target)


@mcp.tool()
async def pikvm_atx_reset(target: str | None = None) -> dict[str, Any]:
    """Pulse the reset button."""
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(atx.atx_reset)
    return await fn(client=_client_for(target), target=target)


# ---------------------------------------------------------------------------
# HID tools (skeleton — wired up but minimal)
# ---------------------------------------------------------------------------


@mcp.tool()
async def pikvm_hid_state(target: str | None = None) -> dict[str, Any]:
    """Get HID subsystem state (keyboard/mouse availability)."""
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(hid.hid_state)
    return await fn(client=_client_for(target), target=target)


@mcp.tool()
async def pikvm_hid_type(text: str, target: str | None = None) -> dict[str, Any]:
    """Type a text string on the target's keyboard."""
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(hid.type_text)
    return await fn(client=_client_for(target), text=text, target=target)


@mcp.tool()
async def pikvm_hid_send_key(
    key: str, state: bool = True, target: str | None = None
) -> dict[str, Any]:
    """Press or release a key. Use PiKVM key names (e.g. 'Enter', 'F12', 'KeyA')."""
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(hid.send_key)
    return await fn(client=_client_for(target), key=key, state=state, target=target)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
