"""Tests for MSD (Mass Storage Device) tools."""

from __future__ import annotations

import asyncio

import pytest
import respx

from pikvm_mcp.client import PiKVMClient
from pikvm_mcp.config import TargetConfig
from pikvm_mcp.tools.msd import (
    UploadProgress,
    _image_name_from_url,
    msd_connect,
    msd_disconnect,
    msd_set_image,
    msd_state,
    msd_upload_url,
)

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

    async def test_msd_upload_url_polls_when_sse_has_no_events(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class FakeClient:
            target_name = "test-kvm"

            def __init__(self) -> None:
                self.calls = 0

            async def stream_sse(self, *args, **kwargs):
                if False:
                    yield {}

            async def get(self, path: str):
                assert path == "/api/msd"
                self.calls += 1
                images = {}
                if self.calls == 2:
                    images["netboot.xyz.iso"] = {
                        "complete": True,
                        "size": 2_424_832,
                    }
                return {"ok": True, "result": {"storage": {"images": images}}}

        async def no_sleep(delay: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", no_sleep)
        result = await msd_upload_url(
            FakeClient(),
            url="https://boot.netboot.xyz/ipxe/netboot.xyz.iso",
            timeout=5,
        )

        assert result["ok"] is True
        assert result["result"]["status"] == "finish"
        assert result["result"]["image"] == "netboot.xyz.iso"
        assert result["result"]["total_bytes"] == 2_424_832

    async def test_msd_upload_url_fallback_post_polls(self) -> None:
        class FakeClient:
            target_name = "test-kvm"

            async def stream_sse(self, *args, **kwargs):
                raise NotImplementedError
                if False:
                    yield {}

            async def post(self, path: str, **kwargs):
                assert path == "/api/msd/write_remote"
                return {"ok": True}

            async def get(self, path: str):
                assert path == "/api/msd"
                return {
                    "ok": True,
                    "result": {
                        "storage": {
                            "images": {
                                "tiny.iso": {"complete": True, "size": 123},
                            },
                        },
                    },
                }

        result = await msd_upload_url(
            FakeClient(),
            url="https://example.com/tiny.iso",
            timeout=5,
        )

        assert result["ok"] is True
        assert result["result"]["image"] == "tiny.iso"
        assert result["result"]["written_bytes"] == 123

    def test_image_name_from_url(self) -> None:
        assert (
            _image_name_from_url("https://example.com/images/ubuntu%2024.04.iso")
            == "ubuntu 24.04.iso"
        )

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
            respx.get(f"{BASE}/api/msd").respond(
                json={"ok": True, "result": {"drive": {"connected": False}}}
            )
            route = respx.post(f"{BASE}/api/msd/set_connected").respond(json={"ok": True})
            result = await msd_connect(client)
            assert result["ok"] is True
            assert "connected=1" in str(route.calls[0].request.url)
        await client.close()

    async def test_msd_connect_already_connected_noops(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            route = respx.get(f"{BASE}/api/msd").respond(
                json={"ok": True, "result": {"drive": {"connected": True}}}
            )
            result = await msd_connect(client)
            assert result["ok"] is True
            assert result["result"]["already_connected"] is True
            assert len(route.calls) == 1
        await client.close()

    async def test_msd_disconnect(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            respx.get(f"{BASE}/api/msd").respond(
                json={"ok": True, "result": {"drive": {"connected": True}}}
            )
            route = respx.post(f"{BASE}/api/msd/set_connected").respond(json={"ok": True})
            result = await msd_disconnect(client)
            assert result["ok"] is True
            assert "connected=0" in str(route.calls[0].request.url)
        await client.close()

    async def test_msd_disconnect_already_disconnected_noops(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            route = respx.get(f"{BASE}/api/msd").respond(
                json={"ok": True, "result": {"drive": {"connected": False}}}
            )
            result = await msd_disconnect(client)
            assert result["ok"] is True
            assert result["result"]["already_disconnected"] is True
            assert len(route.calls) == 1
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
