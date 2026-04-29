"""Tests for Supermicro legacy web tool wrappers."""

from __future__ import annotations


class FakeSupermicroClient:
    async def vm_status(self):
        return {"code": 1, "mounted_status": 0}

    async def vm_config_get(self):
        return {"host": "https://repo", "path": "/mini.iso", "user": "", "password_present": False}

    async def vm_config_set(self, *, host: str, path: str, user: str = "", password: str = ""):
        return {"host": host, "path": path, "user": user, "password_present": bool(password)}

    async def vm_mount(self):
        return {"ok": True}

    async def vm_unmount(self):
        return {"ok": True}

    async def ikvm_jnlp(self):
        return {"title": "ATEN Java iKVM Viewer", "main_class": "tw.com.aten.ikvm.KVMMain"}


class TestSupermicroTools:
    async def test_wrappers(self) -> None:
        from pikvm_mcp.tools import supermicro

        client = FakeSupermicroClient()
        assert await supermicro.vm_status(client) == {"code": 1, "mounted_status": 0}
        assert (await supermicro.vm_config_get(client))["path"] == "/mini.iso"
        assert (await supermicro.vm_config_set(client, host="https://repo", path="/a.iso"))["host"] == "https://repo"
        assert await supermicro.vm_mount(client) == {"ok": True}
        assert await supermicro.vm_unmount(client) == {"ok": True}
        assert (await supermicro.ikvm_jnlp(client))["main_class"] == "tw.com.aten.ikvm.KVMMain"
