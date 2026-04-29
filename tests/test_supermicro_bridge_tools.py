"""Tests for Supermicro bridge tool wrappers."""

from __future__ import annotations

from pathlib import Path


class FakeClient:
    target_name = "sm-lab"

    async def ikvm_jnlp_info(self):
        from pikvm_mcp.supermicro_client import JnlpLaunchInfo

        return JnlpLaunchInfo(
            title="ATEN Java iKVM Viewer",
            vendor="ATEN",
            codebase="http://bmc:80/",
            main_class="tw.com.aten.ikvm.KVMMain",
            jar_href="iKVM.jar",
            arguments=["bmc", "token"],
            raw_jnlp="<jnlp/>",
        )


class TestSupermicroBridgeTools:
    async def test_prepare_bundle(self, tmp_path: Path) -> None:
        from pikvm_mcp.tools import supermicro_bridge

        result = await supermicro_bridge.prepare_ikvm_bundle(
            FakeClient(),
            runtime_dir=tmp_path,
        )
        assert result["target_name"] == "sm-lab"
        assert result["bundle_dir"]

    async def test_stop_bridge(self, monkeypatch) -> None:
        from pikvm_mcp.tools import supermicro_bridge

        monkeypatch.setattr(
            supermicro_bridge,
            "stop_supermicro_bridge",
            lambda container_name: {"container_name": container_name, "stopped": True},
        )
        result = await supermicro_bridge.stop_ikvm_bridge("bridge-1")
        assert result == {"container_name": "bridge-1", "stopped": True}
