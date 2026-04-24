"""Tests for MSD (Mass Storage Device) tools."""

from __future__ import annotations

import pytest
import respx

from pikvm_mcp.client import PiKVMClient
from pikvm_mcp.config import TargetConfig
from pikvm_mcp.tools.msd import UploadProgress, msd_connect, msd_disconnect, msd_set_image, msd_state

BASE = "https://pikvm-test.ts.net:443"


@pytest.fixture
def cfg() -> TargetConfig:
    return TargetConfig(
        name="test-kvm",
        host="pikvm-test.ts.net",
        port=443,
        https=True,
        username="admin",
        password="secret",  # type: ignore[arg-type]
        verify_ssl=False,
    )


class TestMSDTools:
    async def test_msd_state(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            respx.get(f"{BASE}/api/msd").respond(
                json={
                    "ok": True,
                    "result": {
                        "enabled": True,
                        "online": True,
                        "busy": False,
                        "storage": {"size": 32_000_000_000, "free": 28_000_000_000},
                        "drive": {"image": None, "cdrom": True, "connected": False},
                    },
                }
            )
            result = await msd_state(client)
        assert result["ok"] is True
        assert result["result"]["drive"]["connected"] is False
        await client.close()

    async def test_msd_set_image(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            route = respx.post(f"{BASE}/api/msd/set_params").respond(json={"ok": True})
            result = await msd_set_image(client, image="ubuntu-24.04.iso", cdrom=True)
            assert result["ok"] is True
            url = str(route.calls[0].request.url)
            assert "image=ubuntu-24.04.iso" in url
            assert "cdrom=1" in url
        await client.close()

    async def test_msd_connect(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            route = respx.post(f"{BASE}/api/msd/set_connected").respond(json={"ok": True})
            result = await msd_connect(client)
            assert result["ok"] is True
            assert "connected=1" in str(route.calls[0].request.url)
        await client.close()

    async def test_msd_disconnect(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            route = respx.post(f"{BASE}/api/msd/set_connected").respond(json={"ok": True})
            result = await msd_disconnect(client)
            assert result["ok"] is True
            assert "connected=0" in str(route.calls[0].request.url)
        await client.close()


class TestUploadProgress:
    def test_initial_state(self) -> None:
        p = UploadProgress()
        assert p.status == "pending"
        assert p.percent == 0
        assert not p.finished

    def test_update_downloading(self) -> None:
        p = UploadProgress()
        p.update({
            "status": "downloading",
            "percent": 42,
            "total": 1_073_741_824,
            "written": 450_887_680,
        })
        assert p.status == "downloading"
        assert p.percent == 42
        assert p.total_bytes == 1_073_741_824
        assert p.written_bytes == 450_887_680
        assert not p.finished

    def test_update_finish(self) -> None:
        p = UploadProgress()
        p.update({"status": "finish"})
        assert p.finished
        assert p.error is None

    def test_update_error(self) -> None:
        p = UploadProgress()
        p.update({"status": "error", "error": "Network timeout"})
        assert p.finished
        assert p.error == "Network timeout"

    def test_to_dict(self) -> None:
        p = UploadProgress()
        p.update({"status": "downloading", "percent": 75, "total": 1000, "written": 750})
        d = p.to_dict()
        assert d["status"] == "downloading"
        assert d["percent"] == 75
        assert d["total_bytes"] == 1000
        assert d["written_bytes"] == 750
        assert d["error"] is None

    def test_events_accumulate(self) -> None:
        p = UploadProgress()
        p.update({"status": "downloading", "percent": 25})
        p.update({"status": "downloading", "percent": 50})
        p.update({"status": "finish"})
        assert len(p.events) == 3
