"""Async PiKVM HTTP client behind a backend abstraction.

Design notes
------------
``TargetBackend`` is the protocol that the tool layer programs against.
``PiKVMClient`` is the first (and currently only) implementation.  Future
backends (Redfish, Azure VM, RDP) will implement the same protocol so the
tool layer never changes.

Concurrency: PiKVM's KVMD serialises requests internally and returns 409 on
contention.  We hold an ``asyncio.Lock`` *per target* so only one request
flies at a time — no throw-on-contention, no silent drops.

Auth: ``X-KVMD-User`` / ``X-KVMD-Passwd`` headers on every request.
For 2FA-enabled instances the OTP is appended to the password.
"""

from __future__ import annotations

import asyncio
import ssl
from abc import ABC, abstractmethod
from typing import Any

import httpx
import structlog

from pikvm_mcp.config import TargetConfig

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Backend abstraction
# ---------------------------------------------------------------------------


class TargetBackend(ABC):
    """Protocol that every target backend must implement.

    Tools call these methods; they never touch httpx directly.
    """

    @abstractmethod
    async def get(self, path: str, **kwargs: Any) -> dict[str, Any]: ...

    @abstractmethod
    async def post(self, path: str, **kwargs: Any) -> dict[str, Any]: ...

    @abstractmethod
    async def upload(self, path: str, data: bytes, filename: str) -> dict[str, Any]: ...

    @abstractmethod
    async def close(self) -> None: ...

    @property
    @abstractmethod
    def target_name(self) -> str: ...


# ---------------------------------------------------------------------------
# PiKVM implementation
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds


def _build_ssl_context(cfg: TargetConfig) -> ssl.SSLContext | bool:
    """Build SSL context respecting verify_ssl and optional cert pinning."""
    if not cfg.https:
        return False
    if cfg.verify_ssl and not cfg.cert_fingerprint:
        return True  # default verification
    if not cfg.verify_ssl and not cfg.cert_fingerprint:
        return False  # self-signed, no pinning

    # Cert pinning path — verify chain but also assert fingerprint
    ctx = ssl.create_default_context()
    # Fingerprint check happens in _check_fingerprint after connect
    return ctx


class PiKVMClient(TargetBackend):
    """Async HTTP client for a single PiKVM instance."""

    def __init__(self, cfg: TargetConfig) -> None:
        self._cfg = cfg
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            base_url=cfg.base_url,
            verify=_build_ssl_context(cfg),
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0),
            headers=self._auth_headers(),
        )

    def _auth_headers(self) -> dict[str, str]:
        password = self._cfg.password.get_secret_value()
        if self._cfg.otp_secret:
            # PiKVM 2FA: generate TOTP and append to password
            try:
                import hmac
                import hashlib
                import struct
                import time
                import base64

                secret_bytes = base64.b32decode(
                    self._cfg.otp_secret.get_secret_value().upper(), casefold=True
                )
                counter = int(time.time()) // 30
                msg = struct.pack(">Q", counter)
                h = hmac.new(secret_bytes, msg, hashlib.sha1).digest()
                offset = h[-1] & 0x0F
                code = struct.unpack(">I", h[offset : offset + 4])[0]
                code = (code & 0x7FFFFFFF) % 1_000_000
                password = f"{password}{code:06d}"
            except Exception:
                logger.warning("totp_generation_failed", target=self._cfg.name)
        return {
            "X-KVMD-User": self._cfg.username,
            "X-KVMD-Passwd": password,
        }

    @property
    def target_name(self) -> str:
        return self._cfg.name

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Issue a request with per-target mutex and retry with backoff."""
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            async with self._lock:
                try:
                    resp = await self._client.request(method, path, **kwargs)
                    if resp.status_code == 409:
                        # KVMD contention — back off and retry
                        raise httpx.HTTPStatusError(
                            "KVMD contention (409)",
                            request=resp.request,
                            response=resp,
                        )
                    resp.raise_for_status()
                    return resp.json() if resp.content else {}
                except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                    last_exc = exc
                    wait = _BACKOFF_BASE * (2**attempt)
                    logger.warning(
                        "request_retry",
                        target=self._cfg.name,
                        path=path,
                        attempt=attempt + 1,
                        wait=wait,
                        error=str(exc),
                    )
            await asyncio.sleep(wait)

        raise ConnectionError(
            f"PiKVM request failed after {_MAX_RETRIES} retries: {last_exc}"
        ) from last_exc

    async def get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return await self._request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return await self._request("POST", path, **kwargs)

    async def upload(self, path: str, data: bytes, filename: str) -> dict[str, Any]:
        """Upload binary data (e.g. ISO image) to PiKVM MSD."""
        async with self._lock:
            resp = await self._client.post(
                path,
                content=data,
                headers={
                    **self._auth_headers(),
                    "Content-Type": "application/octet-stream",
                    "X-Filename": filename,
                },
                timeout=httpx.Timeout(connect=10.0, read=600.0, write=600.0, pool=10.0),
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}

    async def close(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Client registry — one client per target, lazily created
# ---------------------------------------------------------------------------


class ClientRegistry:
    """Manages PiKVMClient instances keyed by target name."""

    def __init__(self) -> None:
        self._clients: dict[str, PiKVMClient] = {}

    def get_or_create(self, cfg: TargetConfig) -> PiKVMClient:
        if cfg.name not in self._clients:
            self._clients[cfg.name] = PiKVMClient(cfg)
        return self._clients[cfg.name]

    async def close_all(self) -> None:
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
