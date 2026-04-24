"""FastMCP server — tool registration and lifecycle.

Runs over stdio so it can be invoked via ``docker exec``, matching the
unifi-network-mcp deployment pattern.
"""

from __future__ import annotations

import base64
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastmcp import FastMCP

from pikvm_mcp.audit import SessionRecorder, audited
from pikvm_mcp.client import ClientRegistry
from pikvm_mcp.config import AppConfig, load_env_file_from_environment
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
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
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
    env_file = load_env_file_from_environment()
    _config = AppConfig()
    _registry = ClientRegistry()
    _recorder = SessionRecorder(
        audit_dir=_config.audit_dir,
        operator_id=_config.operator_id,
        full_capture=_config.full_capture,
    )
    logger.info(
        "server_started",
        targets=[t.name for t in _config.targets],
        default_target=_config.default_target,
        audit_dir=str(_config.audit_dir),
        full_capture=_config.full_capture,
        env_file=str(env_file) if env_file else None,
    )
    yield
    _recorder.close()
    await _registry.close_all()
    logger.info("server_stopped")


mcp = FastMCP(
    "Agentic-KVM",
    instructions="Bare-metal machine control via PiKVM — MSD, ATX, HID",
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
    downloads the file, not the MCP client.  Streams progress via SSE.
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
# HID tools — full implementation
# ---------------------------------------------------------------------------


@mcp.tool()
async def pikvm_hid_state(target: str | None = None) -> dict[str, Any]:
    """Get HID subsystem state (keyboard/mouse availability, modes)."""
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(hid.hid_state)
    return await fn(client=_client_for(target), target=target)


@mcp.tool()
async def pikvm_screenshot(target: str | None = None) -> dict[str, Any]:
    """Capture a JPEG screenshot of the target's display.

    Returns base64-encoded JPEG data and the detected resolution.
    Also triggers auto-calibration for mouse coordinate mapping.
    """
    recorder = _get_recorder()

    async def _screenshot(*, client: Any, target: str | None = None) -> dict[str, Any]:
        jpeg_data = await hid.screenshot(client)
        try:
            width, height = hid.detect_resolution_from_jpeg(jpeg_data)
        except ValueError:
            width, height = 0, 0
        return {
            "image_base64": base64.b64encode(jpeg_data).decode("ascii"),
            "content_type": "image/jpeg",
            "resolution": {"width": width, "height": height},
            "size_bytes": len(jpeg_data),
        }

    fn = audited(recorder, _resolve_target_name)(_screenshot)
    return await fn(client=_client_for(target), target=target)


@mcp.tool()
async def pikvm_hid_type(text: str, target: str | None = None) -> dict[str, Any]:
    """Type a text string on the target's keyboard.

    Characters are sent one at a time via PiKVM's HID print endpoint.
    Good for typing into login prompts, terminals, BIOS fields, etc.
    """
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(hid.type_text)
    return await fn(client=_client_for(target), text=text, target=target)


@mcp.tool()
async def pikvm_hid_send_key(
    key: str,
    state: bool = True,
    finish: bool = True,
    target: str | None = None,
) -> dict[str, Any]:
    """Press or release a key.

    Key names follow PiKVM/USB-HID convention:
    - Letters: KeyA..KeyZ  - Digits: Digit0..Digit9  - Function: F1..F12
    - Enter, Escape, Backspace, Tab, Space, Delete, Insert
    - ArrowUp/Down/Left/Right, Home, End, PageUp, PageDown
    - Modifiers: ShiftLeft, ControlLeft, AltLeft, MetaLeft (and Right variants)

    state=True means press, state=False means release. finish=True is the safe
    default for one-shot key presses because PiKVM auto-releases non-modifier
    keys. Set finish=False only when intentionally holding a key down before a
    later release event.
    """
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(hid.send_key)
    return await fn(
        client=_client_for(target),
        key=key,
        state=state,
        finish=finish,
        target=target,
    )


@mcp.tool()
async def pikvm_hid_shortcut(
    keys: list[str], hold_ms: int = 100, target: str | None = None
) -> dict[str, Any]:
    """Send a keyboard shortcut (multi-key combo).

    Presses keys in order, holds for hold_ms, then releases in reverse.
    Example: keys=["ControlLeft", "AltLeft", "Delete"] for Ctrl+Alt+Del
    """
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(hid.send_shortcut)
    return await fn(client=_client_for(target), keys=keys, hold_ms=hold_ms, target=target)


@mcp.tool()
async def pikvm_mouse_move(
    x: int, y: int, absolute: bool = True, target: str | None = None
) -> dict[str, Any]:
    """Move the mouse cursor to coordinates.

    With absolute=True (default), x/y are pixel coordinates — auto-calibration
    maps them to PiKVM's center-origin absolute coordinate space.
    With absolute=False, x/y are raw PiKVM absolute coordinates.
    """
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(hid.mouse_move)
    return await fn(client=_client_for(target), x=x, y=y, absolute=absolute, target=target)


@mcp.tool()
async def pikvm_mouse_click(
    button: str = "left",
    x: int | None = None,
    y: int | None = None,
    absolute: bool = True,
    target: str | None = None,
) -> dict[str, Any]:
    """Click a mouse button, optionally at specific coordinates.

    button: "left", "right", or "middle"
    If x/y given, moves there first then clicks.
    Coordinates follow the same absolute/raw rules as mouse_move.
    """
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(hid.mouse_click)
    return await fn(
        client=_client_for(target), button=button, x=x, y=y, absolute=absolute, target=target
    )


@mcp.tool()
async def pikvm_mouse_scroll(
    delta_x: int = 0, delta_y: int = 0, target: str | None = None
) -> dict[str, Any]:
    """Scroll the mouse wheel.

    delta_y: positive = up, negative = down
    delta_x: positive = right, negative = left
    """
    recorder = _get_recorder()
    fn = audited(recorder, _resolve_target_name)(hid.mouse_scroll)
    return await fn(client=_client_for(target), delta_x=delta_x, delta_y=delta_y, target=target)


@mcp.tool()
async def pikvm_hid_calibrate(target: str | None = None) -> dict[str, Any]:
    """Force re-calibration of mouse coordinate mapping.

    Takes a fresh screenshot to detect the current display resolution,
    then updates the pixel-to-HID coordinate mapping.  Call this if the
    target's display resolution changed.
    """
    recorder = _get_recorder()

    async def _recalibrate(*, client: Any, target: str | None = None) -> dict[str, Any]:
        cal = await hid.recalibrate(client)
        return {
            "width": cal.width,
            "height": cal.height,
            "scale_x": round(cal.scale_x, 4),
            "scale_y": round(cal.scale_y, 4),
        }

    fn = audited(recorder, _resolve_target_name)(_recalibrate)
    return await fn(client=_client_for(target), target=target)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
