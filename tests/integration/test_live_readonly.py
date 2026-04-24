"""Read-only live PiKVM integration tests.

Run with:
    PIKVM_INTEGRATION=1 uv run pytest tests/integration -m integration
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from pikvm_mcp.tools import atx, hid, msd

pytestmark = pytest.mark.integration


async def _video_source_online(live_client) -> bool:
    streamer_state = await live_client.get("/api/streamer")
    assert streamer_state["ok"] is True
    source = streamer_state["result"]["streamer"]["source"]
    assert source["resolution"]["width"] > 0
    assert source["resolution"]["height"] > 0
    return bool(source["online"])


async def test_live_backend_readonly(live_client) -> None:
    hid_state = await hid.hid_state(live_client)
    assert hid_state["ok"] is True
    assert hid_state["result"]["online"] is True

    streamer_state = await live_client.get("/api/streamer")
    assert streamer_state["ok"] is True
    assert "source" in streamer_state["result"]["streamer"]

    atx_state = await atx.atx_state(live_client)
    assert atx_state["ok"] is True
    assert "leds" in atx_state["result"]

    msd_state = await msd.msd_state(live_client)
    assert msd_state["ok"] is True
    assert "drive" in msd_state["result"]


async def test_live_backend_screenshot_when_video_online(live_client) -> None:
    if not await _video_source_online(live_client):
        pytest.skip("PiKVM streamer source is offline; screenshot endpoint returns 503")

    screenshot = await hid.screenshot(live_client)
    width, height = hid.detect_resolution_from_jpeg(screenshot)
    assert width > 0
    assert height > 0


async def test_live_mcp_stdio_readonly(
    live_config,
    live_env: dict[str, str],
    repo_root: Path,
    tmp_path: Path,
) -> None:
    assert live_config.targets
    env = {k: v for k, v in os.environ.items() if not k.startswith("PIKVM_")}
    env.update(live_env)
    env["PYTHONPATH"] = "src"

    params = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "pikvm_mcp.server"],
        env=env,
        cwd=repo_root,
    )
    errlog_path = tmp_path / "mcp-stderr.log"

    with errlog_path.open("w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools = await session.list_tools()
                tool_names = {tool.name for tool in tools.tools}
                assert "pikvm_hid_state" in tool_names
                assert "pikvm_screenshot" in tool_names
                assert "pikvm_atx_state" in tool_names
                assert "pikvm_msd_state" in tool_names

                for tool_name in ("pikvm_hid_state", "pikvm_atx_state", "pikvm_msd_state"):
                    result = await session.call_tool(tool_name, {})
                    assert result.isError is False
                    assert result.structuredContent["ok"] is True


async def test_live_mcp_stdio_screenshot_when_video_online(
    live_client,
    live_env: dict[str, str],
    repo_root: Path,
    tmp_path: Path,
) -> None:
    if not await _video_source_online(live_client):
        pytest.skip("PiKVM streamer source is offline; screenshot endpoint returns 503")

    env = {k: v for k, v in os.environ.items() if not k.startswith("PIKVM_")}
    env.update(live_env)
    env["PYTHONPATH"] = "src"

    params = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "pikvm_mcp.server"],
        env=env,
        cwd=repo_root,
    )
    errlog_path = tmp_path / "mcp-stderr.log"

    with errlog_path.open("w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                screenshot = await session.call_tool("pikvm_screenshot", {})
                assert screenshot.isError is False
                payload = screenshot.structuredContent
                assert payload["content_type"] == "image/jpeg"
                assert payload["resolution"]["width"] > 0
                assert payload["resolution"]["height"] > 0
                assert payload["size_bytes"] > 0
                assert payload["image_base64"]
