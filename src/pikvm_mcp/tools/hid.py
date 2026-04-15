"""HID (Human Interface Device) tools — keyboard, mouse, screenshots.

Skeleton for v0.1.  Full implementation in next iteration will include:
- screenshot capture via /api/streamer/snapshot
- type text via /api/hid/print
- send key combo via /api/hid/events
- mouse move/click/scroll via /api/hid/events
- auto-calibration ported from the TS reference

PiKVM API endpoints:
    GET  /api/hid          — HID state (keyboard/mouse availability)
    GET  /api/streamer/snapshot  — JPEG screenshot
    POST /api/hid/print    — type text string
    POST /api/hid/events   — send key/mouse events
"""

from __future__ import annotations

from typing import Any


async def hid_state(client: Any) -> dict[str, Any]:
    """Get current HID state (keyboard/mouse connected, modes)."""
    return await client.get("/api/hid")


async def screenshot(client: Any) -> bytes:
    """Capture a JPEG screenshot of the target's display.

    Returns raw JPEG bytes.  The caller (MCP tool) is responsible for
    base64-encoding or returning as an image content block.
    """
    # Streamer snapshot returns JPEG directly, not JSON
    resp = await client._client.get(  # noqa: SLF001 — low-level access needed for binary
        "/api/streamer/snapshot",
        headers=client._auth_headers(),  # noqa: SLF001
    )
    resp.raise_for_status()
    return resp.content


async def type_text(client: Any, *, text: str) -> dict[str, Any]:
    """Type a string on the target's keyboard."""
    return await client.post("/api/hid/print", params={"text": text})


async def send_key(client: Any, *, key: str, state: bool = True) -> dict[str, Any]:
    """Press or release a single key.

    ``key`` uses PiKVM key names (e.g. 'KeyA', 'Enter', 'F12').
    ``state`` True = press, False = release.
    """
    event = {"key": key, "state": state}
    return await client.post("/api/hid/events", json={"events": [event]})
