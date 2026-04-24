"""Opt-in live HID action tests."""

from __future__ import annotations

import pytest

from pikvm_mcp.tools import hid

pytestmark = [pytest.mark.integration, pytest.mark.hid_action]


async def test_live_mouse_move_center(live_client, hid_actions_enabled) -> None:
    streamer_state = await live_client.get("/api/streamer")
    source = streamer_state["result"]["streamer"]["source"]
    if not source["online"]:
        pytest.skip("PiKVM streamer source is offline; mouse movement cannot be verified")

    await hid.mouse_move(live_client, x=960, y=540, absolute=True)
    screenshot = await hid.screenshot(live_client)
    width, height = hid.detect_resolution_from_jpeg(screenshot)

    assert width > 0
    assert height > 0
