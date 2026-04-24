"""Tests for the PiKVM async HTTP client."""

from __future__ import annotations

import httpx
import pytest
import respx

import pikvm_mcp.client as client_module
from pikvm_mcp.client import PiKVMClient
from pikvm_mcp.config import TargetConfig


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


class TestPiKVMClient:
    async def test_get_success(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            respx.get("https://pikvm-test.ts.net:443/api/atx").respond(
                json={"ok": True, "result": {"leds": {"power": True, "hdd": False}}},
            )
            result = await client.get("/api/atx")
        assert result["ok"] is True
        assert result["result"]["leds"]["power"] is True
        await client.close()

    async def test_auth_headers_present(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            route = respx.get("https://pikvm-test.ts.net:443/api/atx").respond(
                json={"ok": True},
            )
            await client.get("/api/atx")
            req = route.calls[0].request
            assert req.headers["x-kvmd-user"] == "admin"
            assert req.headers["x-kvmd-passwd"] == "secret"
        await client.close()

    async def test_retry_on_409(self, cfg: TargetConfig) -> None:
        """Client should retry on 409 (KVMD contention) and succeed."""
        client = PiKVMClient(cfg)
        call_count = 0

        with respx.mock:
            route = respx.get("https://pikvm-test.ts.net:443/api/atx")

            def side_effect(request: httpx.Request) -> httpx.Response:
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    return httpx.Response(409, json={"ok": False})
                return httpx.Response(200, json={"ok": True})

            route.side_effect = side_effect
            result = await client.get("/api/atx")

        assert result["ok"] is True
        assert call_count == 3
        await client.close()

    async def test_retry_exhausted_raises(self, cfg: TargetConfig) -> None:
        """Client should raise after max retries."""
        client = PiKVMClient(cfg)
        with respx.mock:
            respx.get("https://pikvm-test.ts.net:443/api/atx").respond(
                status_code=409, json={"ok": False}
            )
            with pytest.raises(ConnectionError, match="failed after"):
                await client.get("/api/atx")
        await client.close()

    async def test_post_with_params(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            route = respx.post("https://pikvm-test.ts.net:443/api/atx/power").respond(
                json={"ok": True},
            )
            result = await client.post("/api/atx/power", params={"action": "on"})
            assert result["ok"] is True
            assert "action=on" in str(route.calls[0].request.url)
        await client.close()

    async def test_target_name_property(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        assert client.target_name == "test-kvm"
        await client.close()

    async def test_pinned_client_preflights_before_authenticated_request(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cfg = TargetConfig(
            name="pinned-kvm",
            host="pikvm-test.ts.net",
            port=443,
            https=True,
            username="admin",
            password="secret",  # type: ignore[arg-type]
            cert_fingerprint="aa" * 32,
        )
        calls: list[tuple[str, object]] = []

        async def fake_probe(host: str, port: int, expected: str) -> str:
            calls.append(("probe", (host, port, expected)))
            return "PINNED PEM"

        def fake_build_ssl_context(
            cfg_arg: TargetConfig,
            pinned_cert_pem: str | None = None,
        ) -> bool:
            calls.append(("build", pinned_cert_pem))
            assert cfg_arg is cfg
            return False

        monkeypatch.setattr(client_module, "_probe_fingerprint", fake_probe)
        monkeypatch.setattr(client_module, "_build_ssl_context", fake_build_ssl_context)

        client = PiKVMClient(cfg)
        assert client._client is None  # noqa: SLF001

        with respx.mock:
            respx.get("https://pikvm-test.ts.net:443/api/atx").respond(json={"ok": True})
            result = await client.get("/api/atx")

        assert result["ok"] is True
        assert calls == [
            ("probe", ("pikvm-test.ts.net", 443, "aa" * 32)),
            ("build", "PINNED PEM"),
        ]
        await client.close()
