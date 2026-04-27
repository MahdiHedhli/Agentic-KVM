"""Streamer state + host-wake helper.

The PiKVM streamer captures HDMI from the target.  Its ``/api/streamer``
endpoint exposes whether the video source is currently online (i.e. the
target is awake and outputting video), the detected resolution, the
encoder type, and the set of connected stream clients.

A common operational pattern is "the host went to sleep — wake it up by
jiggling USB HID."  ``wake_host`` automates that: it sends a small burst of
mouse-move + key events (raw HID coordinates so it works without an active
streamer/calibration) and then polls ``/api/streamer`` until the source
comes back online or the timeout elapses.

PiKVM API endpoints used:
    GET /api/streamer  — streamer/source state, clients, encoder
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Streamer state
# ---------------------------------------------------------------------------


def _normalize_streamer_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull the shape we care about out of /api/streamer's nested result.

    The PiKVM payload is roughly::

        {"ok": true, "result": {
            "encoder": {"type": "CPU", ...},
            "source": {"online": true, "resolution": {"width": W, "height": H}, ...},
            "stream": {"clients_stat": {...}, ...}
        }}

    We tolerate missing fields so this works even when the source is
    offline (some keys disappear in that case).
    """
    result = payload.get("result", payload) or {}
    source = result.get("source") or {}
    resolution = source.get("resolution") or {}
    stream = result.get("stream") or {}
    encoder = result.get("encoder") or {}

    clients_stat = stream.get("clients_stat") or {}
    has_clients = bool(clients_stat) if isinstance(clients_stat, dict) else bool(clients_stat)

    return {
        "source_online": bool(source.get("online", False)),
        "source_resolution": {
            "w": int(resolution.get("width", 0) or 0),
            "h": int(resolution.get("height", 0) or 0),
        },
        "has_clients": has_clients,
        "encoder_type": str(encoder.get("type", "")) if encoder else "",
    }


async def streamer_state(client: Any) -> dict[str, Any]:
    """Return a normalized snapshot of /api/streamer.

    Shape::

        {
          "source_online": bool,
          "source_resolution": {"w": int, "h": int},
          "has_clients": bool,
          "encoder_type": str
        }
    """
    payload = await client.get("/api/streamer")
    return _normalize_streamer_payload(payload)


# ---------------------------------------------------------------------------
# Wake host helper
# ---------------------------------------------------------------------------

# Raw HID absolute coordinates — varied positions in the center-origin
# (-32768..32767) space.  Enough movement to register as activity but not
# slamming the cursor into a corner.
_WAKE_MOVES: tuple[tuple[int, int], ...] = (
    (-10000, -10000),
    (10000, -10000),
    (10000, 10000),
    (-10000, 10000),
    (0, 0),
)

# Five auto-released key events.  PiKVM auto-releases non-modifier keys
# when finish=1 (which send_key uses by default), so even ShiftLeft will
# not stay stuck.
_WAKE_KEYS: tuple[str, ...] = (
    "Space",
    "ShiftLeft",
    "Enter",
    "Space",
    "Enter",
)


async def wake_host(
    client: Any,
    *,
    poll_interval: float = 3.0,
    timeout: float = 30.0,
    event_interval: float = 0.2,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    """Jiggle USB HID and poll until the streamer source comes back online.

    Parameters
    ----------
    poll_interval:
        Seconds between streamer-state polls after the wake burst.
    timeout:
        Maximum total time (seconds) to wait for the source to come online.
        Includes the wake burst itself.
    event_interval:
        Delay between individual HID events in the wake burst (seconds).
    sleep:
        Override hook for tests — defaults to ``asyncio.sleep``.

    Returns
    -------
    dict
        - ``{"woke": False, "reason": "already_online", ...}`` if the
          source was already online.
        - ``{"woke": True, "elapsed_seconds": float, "attempts": int, ...}``
          if the source came online within the timeout.
        - ``{"woke": False, "elapsed_seconds": float, "attempts": int,
          "reason": "timeout", ...}`` otherwise.

    The streamer state at exit is included under the ``"state"`` key.
    """
    from pikvm_mcp.tools import hid as hid_mod

    t0 = time.monotonic()

    initial = await streamer_state(client)
    if initial["source_online"]:
        return {
            "woke": False,
            "reason": "already_online",
            "elapsed_seconds": round(time.monotonic() - t0, 3),
            "attempts": 0,
            "state": initial,
        }

    # Wake burst: 5 raw mouse moves + 5 auto-released key events.
    for x, y in _WAKE_MOVES:
        await hid_mod.mouse_move(client, x=x, y=y, absolute=False)
        await sleep(event_interval)

    for key in _WAKE_KEYS:
        await hid_mod.send_key(client, key=key, state=True, finish=True)
        await sleep(event_interval)

    # Poll until online or timeout.
    attempts = 0
    last_state = initial
    while True:
        elapsed = time.monotonic() - t0
        if elapsed >= timeout:
            return {
                "woke": False,
                "reason": "timeout",
                "elapsed_seconds": round(elapsed, 3),
                "attempts": attempts,
                "state": last_state,
            }

        attempts += 1
        try:
            last_state = await streamer_state(client)
        except Exception as exc:  # noqa: BLE001 — streamer can 503 mid-wake
            logger.warning("wake_host_poll_error", attempt=attempts, error=str(exc))
            last_state = {
                "source_online": False,
                "source_resolution": {"w": 0, "h": 0},
                "has_clients": False,
                "encoder_type": "",
            }

        if last_state["source_online"]:
            return {
                "woke": True,
                "elapsed_seconds": round(time.monotonic() - t0, 3),
                "attempts": attempts,
                "state": last_state,
            }

        await sleep(poll_interval)
