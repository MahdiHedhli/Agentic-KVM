"""Opt-in live ATX action tests."""

from __future__ import annotations

import os

import pytest

from pikvm_mcp.tools import atx

pytestmark = [pytest.mark.integration, pytest.mark.atx_action]


async def test_live_atx_explicit_action(live_client, atx_actions_enabled) -> None:
    action = os.environ.get("PIKVM_TEST_ATX_ACTION")
    if not action:
        pytest.skip("set PIKVM_TEST_ATX_ACTION=power_on, reset, power_off, or power_off_hard")

    handlers = {
        "power_on": atx.atx_power_on,
        "reset": atx.atx_reset,
        "power_off": atx.atx_power_off,
        "power_off_hard": atx.atx_power_off_hard,
    }
    if action not in handlers:
        pytest.fail(f"unsupported PIKVM_TEST_ATX_ACTION: {action}")

    state = await atx.atx_state(live_client)
    assert state["ok"] is True
    assert state["result"]["enabled"] is True
    assert state["result"]["busy"] is False

    result = await handlers[action](live_client)
    assert result["ok"] is True
