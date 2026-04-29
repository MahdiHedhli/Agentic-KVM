"""Read-only live IPMI/BMC integration tests.

Run with:
    IPMI_INTEGRATION=1 uv run pytest tests/integration -m ipmi_integration
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from pikvm_mcp.tools import ipmi

pytestmark = pytest.mark.ipmi_integration


async def test_live_ipmi_backend_readonly(live_ipmi_client) -> None:
    power = await ipmi.power_state(live_ipmi_client)
    assert power["powerstate"] in {"on", "off"}

    health = await ipmi.health(live_ipmi_client)
    assert health["health"]["label"] in {"ok", "warning", "critical", "failed", "unknown"}

    sensors = await ipmi.sensors(live_ipmi_client, only_unhealthy=False)
    assert "sensors" in sensors
    assert sensors["count"] == len(sensors["sensors"])

    events = await ipmi.event_log(live_ipmi_client, limit=20)
    assert "events" in events
    assert events["count"] == len(events["events"])


async def test_live_ipmi_mcp_stdio_readonly(
    live_ipmi_config,
    live_ipmi_env: dict[str, str],
    repo_root: Path,
    tmp_path: Path,
) -> None:
    assert live_ipmi_config.ipmi_targets
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PIKVM_", "IPMI_"))}
    env.update(live_ipmi_env)
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
                assert "ipmi_power_state" in tool_names
                assert "ipmi_health" in tool_names
                assert "ipmi_sensors" in tool_names
                assert "ipmi_event_log" in tool_names

                for tool_name in ("ipmi_power_state", "ipmi_health"):
                    result = await session.call_tool(tool_name, {})
                    assert result.isError is False
