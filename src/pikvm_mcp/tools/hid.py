"""HID (Human Interface Device) tools — keyboard, mouse, screenshots.

Full implementation covering:
- Screenshot capture via /api/streamer/snapshot (returns JPEG)
- Type text via /api/hid/print
- Key press/release via /api/hid/events
- Keyboard shortcuts (multi-key combos)
- Mouse move/click/scroll with auto-calibration
- Resolution detection and coordinate mapping (0-65535 HID space)

PiKVM HID mouse operates in absolute mode: coordinates are 0–65535 on
each axis (16-bit unsigned).  Auto-calibration detects the target's
display resolution from screenshots and maps pixel coordinates to HID
space so callers can think in pixels.

PiKVM API endpoints:
    GET  /api/hid                — HID state (keyboard/mouse availability)
    GET  /api/streamer/snapshot  — JPEG screenshot of target display
    POST /api/hid/print          — type text string character by character
    POST /api/hid/events         — send keyboard/mouse events
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()

# PiKVM absolute mouse coordinate space
_HID_ABS_MAX = 65535


# ---------------------------------------------------------------------------
# Auto-calibration: screen resolution → HID coordinate mapping
# ---------------------------------------------------------------------------


@dataclass
class DisplayCalibration:
    """Maps pixel coordinates to PiKVM's 0–65535 HID absolute space."""

    width: int
    height: int

    @property
    def scale_x(self) -> float:
        return _HID_ABS_MAX / self.width

    @property
    def scale_y(self) -> float:
        return _HID_ABS_MAX / self.height

    def pixel_to_hid(self, x: int, y: int) -> tuple[int, int]:
        """Convert pixel coordinates to HID absolute coordinates."""
        hid_x = min(int(x * self.scale_x), _HID_ABS_MAX)
        hid_y = min(int(y * self.scale_y), _HID_ABS_MAX)
        return hid_x, hid_y


def detect_resolution_from_jpeg(data: bytes) -> tuple[int, int]:
    """Extract width and height from JPEG binary data.

    Parses JPEG markers to find the SOF (Start of Frame) segment which
    contains the image dimensions.  No external dependency needed.

    Returns (width, height).
    """
    if len(data) < 4 or data[0:2] != b"\xff\xd8":
        raise ValueError("Not a valid JPEG file")

    offset = 2
    while offset < len(data) - 1:
        if data[offset] != 0xFF:
            raise ValueError(f"Invalid JPEG marker at offset {offset}")

        marker = data[offset + 1]

        # Skip padding 0xFF bytes
        if marker == 0xFF:
            offset += 1
            continue

        # SOF markers (SOF0 through SOF15, excluding DHT=0xC4 and DAC=0xCC)
        # Common ones: 0xC0 (baseline), 0xC2 (progressive)
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                       0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            # SOF segment: length(2) + precision(1) + height(2) + width(2)
            if offset + 9 > len(data):
                raise ValueError("Truncated SOF segment")
            height = struct.unpack(">H", data[offset + 5 : offset + 7])[0]
            width = struct.unpack(">H", data[offset + 7 : offset + 9])[0]
            return width, height

        # Not a SOF marker — skip this segment
        if offset + 3 >= len(data):
            break
        seg_len = struct.unpack(">H", data[offset + 2 : offset + 4])[0]
        offset += 2 + seg_len

    raise ValueError("No SOF marker found — could not determine JPEG dimensions")


# Per-target calibration cache
_calibrations: dict[str, DisplayCalibration] = {}


async def calibrate(client: Any) -> DisplayCalibration:
    """Auto-calibrate by taking a screenshot and detecting resolution.

    Caches the result per target_name.  Call ``recalibrate`` to force refresh.
    """
    target = client.target_name
    if target in _calibrations:
        return _calibrations[target]

    jpeg_data = await screenshot_raw(client)
    width, height = detect_resolution_from_jpeg(jpeg_data)
    cal = DisplayCalibration(width=width, height=height)
    _calibrations[target] = cal
    logger.info(
        "hid_calibrated",
        target=target,
        width=width,
        height=height,
        scale_x=round(cal.scale_x, 2),
        scale_y=round(cal.scale_y, 2),
    )
    return cal


async def recalibrate(client: Any) -> DisplayCalibration:
    """Force re-detection of display resolution."""
    target = client.target_name
    _calibrations.pop(target, None)
    return await calibrate(client)


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------


async def screenshot_raw(client: Any) -> bytes:
    """Capture a JPEG screenshot — returns raw bytes."""
    return await client.get_raw("/api/streamer/snapshot")


async def screenshot(client: Any) -> bytes:
    """Capture a JPEG screenshot of the target's display.

    Returns raw JPEG bytes.  Also triggers auto-calibration if not
    already done for this target.
    """
    data = await screenshot_raw(client)
    # Opportunistically calibrate on first screenshot
    target = client.target_name
    if target not in _calibrations:
        try:
            width, height = detect_resolution_from_jpeg(data)
            _calibrations[target] = DisplayCalibration(width=width, height=height)
        except ValueError:
            pass  # Non-fatal — calibration just won't be cached
    return data


# ---------------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------------


async def hid_state(client: Any) -> dict[str, Any]:
    """Get current HID state (keyboard/mouse connected, modes)."""
    return await client.get("/api/hid")


async def type_text(client: Any, *, text: str) -> dict[str, Any]:
    """Type a string on the target's keyboard.

    PiKVM's /api/hid/print types character-by-character with appropriate
    key events.  Suitable for typing into login prompts, terminals, etc.
    """
    return await client.post("/api/hid/print", params={"text": text})


async def send_key(client: Any, *, key: str, state: bool = True) -> dict[str, Any]:
    """Press or release a single key.

    ``key`` uses PiKVM/USB-HID key names:
        Letters:  KeyA, KeyB, ..., KeyZ
        Digits:   Digit0, Digit1, ..., Digit9
        Function: F1, F2, ..., F12
        Special:  Enter, Escape, Backspace, Tab, Space, Delete, Insert
        Arrows:   ArrowUp, ArrowDown, ArrowLeft, ArrowRight
        Modifiers: ShiftLeft, ShiftRight, ControlLeft, ControlRight,
                   AltLeft, AltRight, MetaLeft, MetaRight
        Navigation: Home, End, PageUp, PageDown

    ``state`` True = press, False = release.
    """
    event = {"key": key, "state": state}
    return await client.post("/api/hid/events", json={"events": [event]})


async def send_shortcut(
    client: Any, *, keys: list[str], hold_ms: int = 100
) -> dict[str, Any]:
    """Send a keyboard shortcut (e.g. Ctrl+Alt+Delete).

    Presses keys in order, waits ``hold_ms`` milliseconds, then releases
    in reverse order.  This mimics natural keyboard shortcut behavior.

    Example: ``keys=["ControlLeft", "AltLeft", "Delete"]``
    """
    events: list[dict[str, Any]] = []

    # Press all keys in order
    for key in keys:
        events.append({"key": key, "state": True})

    result = await client.post("/api/hid/events", json={"events": events})

    # Hold briefly
    await asyncio.sleep(hold_ms / 1000.0)

    # Release all keys in reverse order
    release_events = [{"key": key, "state": False} for key in reversed(keys)]
    await client.post("/api/hid/events", json={"events": release_events})

    return result


# ---------------------------------------------------------------------------
# Mouse
# ---------------------------------------------------------------------------


async def mouse_move(
    client: Any, *, x: int, y: int, absolute: bool = True
) -> dict[str, Any]:
    """Move the mouse cursor.

    If ``absolute=True`` (default), x/y are pixel coordinates that get
    auto-calibrated to HID space (0–65535).  Requires a prior screenshot
    or explicit calibrate() call.

    If ``absolute=False``, x/y are raw HID coordinates (0–65535).
    """
    if absolute:
        cal = await calibrate(client)
        hid_x, hid_y = cal.pixel_to_hid(x, y)
    else:
        hid_x = min(max(x, 0), _HID_ABS_MAX)
        hid_y = min(max(y, 0), _HID_ABS_MAX)

    event = {
        "mouse_move": {"x": hid_x, "y": hid_y},
    }
    return await client.post("/api/hid/events", json={"events": [event]})


async def mouse_click(
    client: Any,
    *,
    button: str = "left",
    x: int | None = None,
    y: int | None = None,
    absolute: bool = True,
) -> dict[str, Any]:
    """Click a mouse button, optionally at specific coordinates.

    ``button``: "left", "right", or "middle"
    If x/y are provided, moves to that position first.
    """
    button_map = {"left": "left", "right": "right", "middle": "middle"}
    btn = button_map.get(button.lower(), "left")

    events: list[dict[str, Any]] = []

    # Move first if coordinates given
    if x is not None and y is not None:
        if absolute:
            cal = await calibrate(client)
            hid_x, hid_y = cal.pixel_to_hid(x, y)
        else:
            hid_x = min(max(x, 0), _HID_ABS_MAX)
            hid_y = min(max(y, 0), _HID_ABS_MAX)
        events.append({"mouse_move": {"x": hid_x, "y": hid_y}})

    # Press and release
    events.append({"mouse_button": {"button": btn, "state": True}})
    events.append({"mouse_button": {"button": btn, "state": False}})

    return await client.post("/api/hid/events", json={"events": events})


async def mouse_scroll(
    client: Any,
    *,
    delta_x: int = 0,
    delta_y: int = 0,
) -> dict[str, Any]:
    """Scroll the mouse wheel.

    ``delta_y``: positive = scroll up, negative = scroll down
    ``delta_x``: positive = scroll right, negative = scroll left
    """
    event = {
        "mouse_wheel": {"x": delta_x, "y": delta_y},
    }
    return await client.post("/api/hid/events", json={"events": [event]})
