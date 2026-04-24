"""Opt-in live MSD action tests."""

from __future__ import annotations

import os

import pytest

from pikvm_mcp.tools import msd

pytestmark = [pytest.mark.integration, pytest.mark.msd_action]


def _image_name(value):
    if isinstance(value, dict):
        return value.get("name")
    return value


async def test_live_msd_connect_disconnect_existing_image(
    live_client,
    msd_actions_enabled,
) -> None:
    image = os.environ.get("PIKVM_TEST_MSD_IMAGE", "netboot.xyz.iso")
    state = await msd.msd_state(live_client)
    images = state["result"]["storage"]["images"]
    if image not in images:
        pytest.skip(f"MSD image not present on PiKVM storage: {image}")

    await msd.msd_set_image(live_client, image=image, cdrom=True)
    await msd.msd_connect(live_client)
    connected_state = await msd.msd_state(live_client)
    drive = connected_state["result"]["drive"]
    assert drive["connected"] is True
    assert _image_name(drive["image"]) == image
    assert drive["cdrom"] is True

    await msd.msd_disconnect(live_client)
    final_state = await msd.msd_state(live_client)
    assert final_state["result"]["drive"]["connected"] is False
