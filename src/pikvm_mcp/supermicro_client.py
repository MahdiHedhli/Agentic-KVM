"""Async client for Supermicro legacy web iKVM / virtual-media CGI.

This is intentionally separate from the generic IPMI client.  These calls are
vendor-specific and ride the BMC web UI, not the IPMI protocol itself.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import ssl
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from pikvm_mcp.config import IpmiTargetConfig

logger = structlog.get_logger()


class SupermicroWebError(Exception):
    """Raised for Supermicro web-UI login, CSRF, or CGI failures."""


_TOKEN_RE = re.compile(r'SmcCsrfInsert \("CSRF_TOKEN", "([^"]+)"\)')


def _normalize_fingerprint(fp: str) -> str:
    return fp.replace(":", "").replace(" ", "").lower()


def _verify_cert_fingerprint(der_cert: bytes, expected_fingerprint: str) -> str:
    expected_norm = _normalize_fingerprint(expected_fingerprint)
    actual = hashlib.sha256(der_cert).hexdigest()
    if actual != expected_norm:
        raise SupermicroWebError(
            f"Certificate fingerprint mismatch: expected {expected_norm}, got {actual}"
        )
    return actual


async def _probe_fingerprint(host: str, port: int, expected: str) -> str:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    _reader, writer = await asyncio.open_connection(host, port, ssl=ctx)
    try:
        ssl_object = writer.get_extra_info("ssl_object")
        if ssl_object is None:
            raise SupermicroWebError("No TLS connection — cannot verify fingerprint")
        der_cert = ssl_object.getpeercert(binary_form=True)
        if der_cert is None:
            raise SupermicroWebError("No peer certificate presented")
        _verify_cert_fingerprint(der_cert, expected)
        logger.info("supermicro_cert_pinning_verified")
        return ssl.DER_cert_to_PEM_cert(der_cert)
    finally:
        writer.close()
        await writer.wait_closed()


def _build_ssl_context(
    cfg: IpmiTargetConfig,
    pinned_cert_pem: str | None = None,
) -> ssl.SSLContext | bool:
    if not cfg.web_https:
        return False
    if not cfg.web_cert_fingerprint:
        return cfg.web_verify_ssl
    if pinned_cert_pem is None:
        raise ValueError("Pinned certificate PEM is required when web_cert_fingerprint is set")
    ctx = ssl.create_default_context(cadata=pinned_cert_pem)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def _parse_xml(xml_text: str) -> ET.Element:
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SupermicroWebError("Supermicro CGI returned invalid XML") from exc


def _require_token(page_html: str) -> str:
    match = _TOKEN_RE.search(page_html)
    if not match:
        raise SupermicroWebError("Authenticated page did not expose a CSRF token")
    return match.group(1)


def _jnlp_text_is_authenticated(jnlp_text: str) -> bool:
    return "<jnlp " in jnlp_text and "ATEN Java iKVM Viewer" in jnlp_text


@dataclass(slots=True)
class JnlpLaunchInfo:
    title: str
    vendor: str
    codebase: str
    main_class: str
    jar_href: str
    arguments: list[str]
    raw_jnlp: str


def _parse_jnlp(jnlp_text: str) -> JnlpLaunchInfo:
    root = _parse_xml(jnlp_text)
    info = root.find("information")
    resources = root.find("resources")
    app_desc = root.find("application-desc")
    if info is None or resources is None or app_desc is None:
        raise SupermicroWebError("JNLP is missing required sections")

    title = (info.findtext("title") or "").strip()
    vendor = (info.findtext("vendor") or "").strip()
    jar = resources.find("jar")
    if jar is None:
        raise SupermicroWebError("JNLP did not include a KVM viewer JAR")

    arguments = [(arg.text or "").strip() for arg in app_desc.findall("argument")]
    return JnlpLaunchInfo(
        title=title,
        vendor=vendor,
        codebase=root.attrib.get("codebase", ""),
        main_class=app_desc.attrib.get("main-class", ""),
        jar_href=jar.attrib.get("href", ""),
        arguments=arguments,
        raw_jnlp=jnlp_text,
    )


class SupermicroClient:
    """Async facade for Supermicro legacy web iKVM and virtual media."""

    def __init__(self, cfg: IpmiTargetConfig) -> None:
        self._cfg = cfg
        self._lock = asyncio.Lock()
        self._fingerprint_verified = False
        self._pinned_cert_pem: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._csrf_token: str | None = None
        if not cfg.web_cert_fingerprint:
            self._client = self._new_http_client(_build_ssl_context(cfg))

    @property
    def target_name(self) -> str:
        return self._cfg.name

    def _new_http_client(self, verify: ssl.SSLContext | bool) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._cfg.web_base_url,
            verify=verify,
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0),
            follow_redirects=True,
        )

    async def _ensure_fingerprint(self) -> None:
        if self._fingerprint_verified or not self._cfg.web_cert_fingerprint:
            return
        self._pinned_cert_pem = await _probe_fingerprint(
            self._cfg.host, self._cfg.web_port, self._cfg.web_cert_fingerprint
        )
        self._fingerprint_verified = True

    async def _ensure_client(self) -> httpx.AsyncClient:
        await self._ensure_fingerprint()
        if self._client is None:
            self._client = self._new_http_client(
                _build_ssl_context(self._cfg, self._pinned_cert_pem)
            )
        return self._client

    async def _login(self) -> None:
        client = await self._ensure_client()
        resp = await client.post(
            "/cgi/login.cgi",
            data={
                "name": self._cfg.username,
                "pwd": self._cfg.password.get_secret_value(),
            },
        )
        resp.raise_for_status()
        if "SID" not in client.cookies:
            raise SupermicroWebError("Supermicro login did not yield a SID cookie")

    async def _ensure_authenticated(self, csrf_page: str = "/cgi/url_redirect.cgi?url_name=vm_cdrom") -> httpx.AsyncClient:
        client = await self._ensure_client()
        if "SID" not in client.cookies:
            await self._login()
        if self._csrf_token is None:
            page = await client.get(csrf_page)
            page.raise_for_status()
            self._csrf_token = _require_token(page.text)
            client.headers["CSRF_TOKEN"] = self._csrf_token
            client.headers["Referer"] = f"{self._cfg.web_base_url}{csrf_page}"
        return client

    async def _post_form(
        self,
        path: str,
        data: dict[str, Any],
        *,
        csrf_page: str = "/cgi/url_redirect.cgi?url_name=vm_cdrom",
    ) -> httpx.Response:
        client = await self._ensure_authenticated(csrf_page=csrf_page)
        resp = await client.post(path, data=data)
        resp.raise_for_status()
        if "Token Value is not matched." in resp.text:
            self._csrf_token = None
            client.headers.pop("CSRF_TOKEN", None)
            client = await self._ensure_authenticated(csrf_page=csrf_page)
            resp = await client.post(path, data=data)
            resp.raise_for_status()
        return resp

    async def vm_status(self) -> dict[str, Any]:
        async with self._lock:
            resp = await self._post_form("/cgi/op.cgi", {"op": "vm_status", "time_stamp": "now"})
        root = _parse_xml(resp.text)
        code = root.find("CODE")
        devices = []
        for dev in root.findall("DEVICE"):
            devices.append({"id": int(dev.attrib.get("ID", "0")), "status": int(dev.attrib.get("STATUS", "0"))})
        return {
            "code": int(code.attrib.get("NO", "0")) if code is not None else 0,
            "mounted_status": int(code.attrib.get("STATUS", "0")) if code is not None else 0,
            "devices": devices,
        }

    async def vm_config_get(self) -> dict[str, Any]:
        async with self._lock:
            resp = await self._post_form(
                "/cgi/ipmi.cgi",
                {"VIRTUAL_MEDIA_SHARE_IMAGE.XML": "(0,0)"},
            )
        root = _parse_xml(resp.text)
        info = root.find("VM_INFO")
        if info is None:
            raise SupermicroWebError("VM_INFO missing from virtual media config response")
        return {
            "host": info.attrib.get("HOST", ""),
            "path": info.attrib.get("PATH", ""),
            "user": info.attrib.get("USER", ""),
            "password_present": bool(info.attrib.get("PWD", "")),
        }

    async def vm_config_set(
        self,
        *,
        host: str,
        path: str,
        user: str = "",
        password: str = "",
    ) -> dict[str, Any]:
        async with self._lock:
            resp = await self._post_form(
                "/cgi/op.cgi",
                {
                    "op": "config_iso",
                    "host": host,
                    "path": path,
                    "user": user,
                    "pwd": password,
                },
            )
        return {"ok": "OK" in resp.text.upper() or resp.text.strip() == "", "response_text": resp.text.strip()}

    async def vm_mount(self) -> dict[str, Any]:
        async with self._lock:
            resp = await self._post_form("/cgi/op.cgi", {"op": "mount_iso", "time_stamp": "now"})
        return {"ok": "ERROR" not in resp.text.upper(), "response_text": resp.text.strip()}

    async def vm_unmount(self) -> dict[str, Any]:
        async with self._lock:
            resp = await self._post_form("/cgi/op.cgi", {"op": "umount_iso", "time_stamp": "now"})
        return {"ok": "ERROR" not in resp.text.upper(), "response_text": resp.text.strip()}

    async def ikvm_jnlp(self) -> dict[str, Any]:
        async with self._lock:
            client = await self._ensure_authenticated(
                csrf_page="/cgi/url_redirect.cgi?url_name=man_ikvm"
            )
            resp = await client.get("/cgi/url_redirect.cgi?url_name=ikvm&url_type=jwsk")
            resp.raise_for_status()
        if not _jnlp_text_is_authenticated(resp.text):
            raise SupermicroWebError("BMC returned HTML instead of authenticated iKVM JNLP")
        jnlp = _parse_jnlp(resp.text)
        return {
            "title": jnlp.title,
            "vendor": jnlp.vendor,
            "codebase": jnlp.codebase,
            "main_class": jnlp.main_class,
            "jar_href": jnlp.jar_href,
            "arguments": jnlp.arguments,
            "raw_jnlp": jnlp.raw_jnlp,
        }

    async def ikvm_jnlp_info(self) -> JnlpLaunchInfo:
        async with self._lock:
            client = await self._ensure_authenticated(
                csrf_page="/cgi/url_redirect.cgi?url_name=man_ikvm"
            )
            resp = await client.get("/cgi/url_redirect.cgi?url_name=ikvm&url_type=jwsk")
            resp.raise_for_status()
        if not _jnlp_text_is_authenticated(resp.text):
            raise SupermicroWebError("BMC returned HTML instead of authenticated iKVM JNLP")
        return _parse_jnlp(resp.text)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()


class SupermicroClientRegistry:
    """Manages SupermicroClient instances keyed by target name."""

    def __init__(self) -> None:
        self._clients: dict[str, SupermicroClient] = {}

    def get_or_create(self, cfg: IpmiTargetConfig) -> SupermicroClient:
        if cfg.vendor.lower() != "supermicro":
            raise ValueError(
                f"Target {cfg.name!r} vendor is {cfg.vendor!r}; Supermicro web tools require vendor=supermicro"
            )
        if cfg.name not in self._clients:
            self._clients[cfg.name] = SupermicroClient(cfg)
        return self._clients[cfg.name]

    async def close_all(self) -> None:
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
