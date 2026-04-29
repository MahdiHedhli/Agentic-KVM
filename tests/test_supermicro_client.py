"""Tests for the Supermicro legacy web client."""

from __future__ import annotations

import httpx
import pytest
import respx

from pikvm_mcp.config import IpmiTargetConfig
from pikvm_mcp.supermicro_client import SupermicroClient, SupermicroWebError


def _cfg() -> IpmiTargetConfig:
    return IpmiTargetConfig(
        name="sm-lab",
        host="bmc.ts.net",
        username="operator",
        password="secret",
        vendor="supermicro",
    )


VM_PAGE = """
<html><body>
<script src="../js/utils.js"></script>
<script>SmcCsrfInsert ("CSRF_TOKEN", "csrf-token-123");</script>
</body></html>
"""

JNLP = """<?xml version="1.0" encoding="utf-8"?>
<jnlp spec="1.0+" codebase="http://bmc.ts.net:80/">
  <information>
    <title>ATEN Java iKVM Viewer</title>
    <vendor>ATEN</vendor>
  </information>
  <resources>
    <jar href="iKVM__V1.69.38.0x0.jar"/>
  </resources>
  <application-desc main-class="tw.com.aten.ikvm.KVMMain">
    <argument>bmc.ts.net</argument>
    <argument>token-one</argument>
    <argument>operator</argument>
    <argument>5900</argument>
  </application-desc>
</jnlp>
"""


class TestSupermicroClient:
    @respx.mock
    async def test_vm_status_uses_login_cookie_and_csrf_header(self) -> None:
        client = SupermicroClient(_cfg())

        login = respx.post("https://bmc.ts.net:443/cgi/login.cgi").mock(
            return_value=httpx.Response(
                200,
                headers={"set-cookie": "SID=session-123; path=/; Secure; HttpOnly"},
                text="<html/>",
            )
        )
        page = respx.get("https://bmc.ts.net:443/cgi/url_redirect.cgi?url_name=vm_cdrom").mock(
            return_value=httpx.Response(200, text=VM_PAGE)
        )

        def _vm_status(request: httpx.Request) -> httpx.Response:
            assert request.headers["csrf_token"] == "csrf-token-123"
            assert request.headers["referer"] == "https://bmc.ts.net:443/cgi/url_redirect.cgi?url_name=vm_cdrom"
            assert request.content.decode() == "op=vm_status&time_stamp=now"
            return httpx.Response(
                200,
                text='<?xml version="1.0"?><VM><CODE NO="1" STATUS="0"/><DEVICE ID="0" STATUS="255"/></VM>',
            )

        vm_status = respx.post("https://bmc.ts.net:443/cgi/op.cgi").mock(side_effect=_vm_status)

        result = await client.vm_status()

        assert login.called
        assert page.called
        assert vm_status.called
        assert result["code"] == 1
        assert result["mounted_status"] == 0
        assert result["devices"] == [{"id": 0, "status": 255}]
        await client.close()

    @respx.mock
    async def test_vm_config_get_parses_vm_info(self) -> None:
        client = SupermicroClient(_cfg())
        respx.post("https://bmc.ts.net:443/cgi/login.cgi").mock(
            return_value=httpx.Response(
                200,
                headers={"set-cookie": "SID=session-123; path=/; Secure; HttpOnly"},
                text="<html/>",
            )
        )
        respx.get("https://bmc.ts.net:443/cgi/url_redirect.cgi?url_name=vm_cdrom").mock(
            return_value=httpx.Response(200, text=VM_PAGE)
        )
        respx.post("https://bmc.ts.net:443/cgi/ipmi.cgi").mock(
            return_value=httpx.Response(
                200,
                text='<?xml version="1.0"?><IPMI><VM_INFO HOST="https://repo" PATH="/mini.iso" USER="lab" PWD="set"/></IPMI>',
            )
        )

        result = await client.vm_config_get()

        assert result == {
            "host": "https://repo",
            "path": "/mini.iso",
            "user": "lab",
            "password_present": True,
        }
        await client.close()

    @respx.mock
    async def test_ikvm_jnlp_parses_launch_descriptor(self) -> None:
        client = SupermicroClient(_cfg())
        respx.post("https://bmc.ts.net:443/cgi/login.cgi").mock(
            return_value=httpx.Response(
                200,
                headers={"set-cookie": "SID=session-123; path=/; Secure; HttpOnly"},
                text="<html/>",
            )
        )
        respx.get("https://bmc.ts.net:443/cgi/url_redirect.cgi?url_name=man_ikvm").mock(
            return_value=httpx.Response(200, text=VM_PAGE)
        )
        respx.get("https://bmc.ts.net:443/cgi/url_redirect.cgi?url_name=ikvm&url_type=jwsk").mock(
            return_value=httpx.Response(200, text=JNLP)
        )

        result = await client.ikvm_jnlp()

        assert result["title"] == "ATEN Java iKVM Viewer"
        assert result["jar_href"] == "iKVM__V1.69.38.0x0.jar"
        assert result["main_class"] == "tw.com.aten.ikvm.KVMMain"
        assert result["arguments"][0] == "bmc.ts.net"
        await client.close()

    @respx.mock
    async def test_bad_jnlp_raises(self) -> None:
        client = SupermicroClient(_cfg())
        respx.post("https://bmc.ts.net:443/cgi/login.cgi").mock(
            return_value=httpx.Response(
                200,
                headers={"set-cookie": "SID=session-123; path=/; Secure; HttpOnly"},
                text="<html/>",
            )
        )
        respx.get("https://bmc.ts.net:443/cgi/url_redirect.cgi?url_name=man_ikvm").mock(
            return_value=httpx.Response(200, text=VM_PAGE)
        )
        respx.get("https://bmc.ts.net:443/cgi/url_redirect.cgi?url_name=ikvm&url_type=jwsk").mock(
            return_value=httpx.Response(200, text="<html>login</html>")
        )

        with pytest.raises(SupermicroWebError):
            await client.ikvm_jnlp()
        await client.close()
