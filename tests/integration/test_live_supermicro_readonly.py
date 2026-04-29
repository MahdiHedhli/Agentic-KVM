"""Read-only live Supermicro legacy web integration tests.

Run with:
    IPMI_INTEGRATION=1 uv run pytest tests/integration -m ipmi_integration
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from pikvm_mcp.supermicro_client import SupermicroClient

pytestmark = pytest.mark.ipmi_integration


async def test_live_supermicro_backend_readonly(live_ipmi_config) -> None:
    target = live_ipmi_config.resolve_ipmi_target()
    if target.vendor.lower() != "supermicro":
        pytest.skip("legacy Supermicro web tests only apply to vendor=supermicro targets")

    client = SupermicroClient(target)
    try:
        status = await client.vm_status()
        assert "devices" in status

        config = await client.vm_config_get()
        assert "host" in config
        assert "path" in config

        jnlp = await client.ikvm_jnlp()
        assert jnlp["title"] == "ATEN Java iKVM Viewer"
        assert jnlp["main_class"]
        assert jnlp["jar_href"]
    finally:
        await client.close()


async def test_live_supermicro_mcp_stdio_readonly(
    live_ipmi_config,
    live_ipmi_env: dict[str, str],
    repo_root: Path,
    tmp_path: Path,
) -> None:
    target = live_ipmi_config.resolve_ipmi_target()
    if target.vendor.lower() != "supermicro":
        pytest.skip("legacy Supermicro web tests only apply to vendor=supermicro targets")

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
                assert "supermicro_vm_status" in tool_names
                assert "supermicro_vm_config_get" in tool_names
                assert "supermicro_ikvm_jnlp" in tool_names

                for tool_name in (
                    "supermicro_vm_status",
                    "supermicro_vm_config_get",
                    "supermicro_ikvm_jnlp",
                ):
                    result = await session.call_tool(tool_name, {})
                    assert result.isError is False
