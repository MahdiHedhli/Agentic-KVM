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
import hashlib
import ssl
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
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
    async def get_raw(self, path: str, **kwargs: Any) -> bytes:
        """GET that returns raw bytes (e.g. JPEG screenshots)."""
        ...

    @abstractmethod
    async def stream_sse(self, path: str, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        """POST that streams SSE events (e.g. MSD upload progress)."""
        ...

    @abstractmethod
    async def upload(self, path: str, data: bytes, filename: str) -> dict[str, Any]: ...

    @abstractmethod
    async def close(self) -> None: ...

    @property
    @abstractmethod
    def target_name(self) -> str: ...


# ---------------------------------------------------------------------------
# Cert pinning
# ---------------------------------------------------------------------------


class CertificatePinningError(Exception):
    """Raised when the server certificate fingerprint doesn't match the pinned value."""


def _normalize_fingerprint(fp: str) -> str:
    """Normalize a hex fingerprint to lowercase without separators."""
    return fp.replace(":", "").replace(" ", "").lower()


def _verify_cert_fingerprint(der_cert: bytes, expected_fingerprint: str) -> str:
    """Verify a DER certificate's SHA-256 fingerprint.

    Returns the normalized actual fingerprint.  Raises ``CertificatePinningError``
    if the expected and actual fingerprints differ.
    """
    expected_norm = _normalize_fingerprint(expected_fingerprint)
    actual = hashlib.sha256(der_cert).hexdigest()
    if actual != expected_norm:
        raise CertificatePinningError(
            f"Certificate fingerprint mismatch: expected {expected_norm}, got {actual}"
        )
    return actual


async def _probe_fingerprint(host: str, port: int, expected: str) -> str:
    """Open a raw TLS connection, verify the fingerprint, and return cert PEM.

    This preflight sends no PiKVM credentials.  The returned certificate is then
    used as the only trust root for the authenticated HTTP client.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # we're doing our own verification

    _reader, writer = await asyncio.open_connection(host, port, ssl=ctx)
    try:
        ssl_object = writer.get_extra_info("ssl_object")
        if ssl_object is None:
            raise CertificatePinningError("No TLS connection — cannot verify fingerprint")

        der_cert = ssl_object.getpeercert(binary_form=True)
        if der_cert is None:
            raise CertificatePinningError("No peer certificate presented")

        actual = _verify_cert_fingerprint(der_cert, expected)
        logger.info(
            "cert_pinning_verified",
            host=host,
            fingerprint=actual,
        )
        return ssl.DER_cert_to_PEM_cert(der_cert)
    finally:
        writer.close()
        await writer.wait_closed()


# ---------------------------------------------------------------------------
# PiKVM implementation
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds


def _build_ssl_context(
    cfg: TargetConfig,
    pinned_cert_pem: str | None = None,
) -> ssl.SSLContext | bool:
    """Build SSL context respecting verify_ssl and optional cert pinning."""
    if not cfg.https:
        return False
    if not cfg.cert_fingerprint:
        # No pinning — use verify_ssl as-is
        return cfg.verify_ssl

    if pinned_cert_pem is None:
        raise ValueError("Pinned certificate PEM is required when cert_fingerprint is set")

    # PiKVM-first pinning path: trust only the preflight-verified certificate
    # for the real authenticated HTTP client. Hostname checks are disabled
    # because PiKVM self-signed certificates commonly do not match Tailnet names.
    ctx = ssl.create_default_context(cadata=pinned_cert_pem)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


class PiKVMClient(TargetBackend):
    """Async HTTP client for a single PiKVM instance."""

    def __init__(self, cfg: TargetConfig) -> None:
        self._cfg = cfg
        self._lock = asyncio.Lock()
        self._fingerprint_verified = False
        self._pinned_cert_pem: str | None = None
        self._client: httpx.AsyncClient | None = None
        if not cfg.cert_fingerprint:
            self._client = self._new_http_client(_build_ssl_context(cfg))

    def _new_http_client(self, verify: ssl.SSLContext | bool) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._cfg.base_url,
            verify=verify,
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0),
            headers=self._auth_headers(),
        )

    def _auth_headers(self) -> dict[str, str]:
        password = self._cfg.password.get_secret_value()
        if self._cfg.otp_secret:
            # PiKVM 2FA: generate TOTP and append to password
            try:
                import hmac
                import hashlib as _hashlib
                import struct
                import time
                import base64

                secret_bytes = base64.b32decode(
                    self._cfg.otp_secret.get_secret_value().upper(), casefold=True
                )
                counter = int(time.time()) // 30
                msg = struct.pack(">Q", counter)
                h = hmac.new(secret_bytes, msg, _hashlib.sha1).digest()
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

    async def _ensure_fingerprint(self) -> None:
        """Verify cert fingerprint on first use (if pinning is configured)."""
        if self._fingerprint_verified or not self._cfg.cert_fingerprint:
            return
        self._pinned_cert_pem = await _probe_fingerprint(
            self._cfg.host,
            self._cfg.port,
            self._cfg.cert_fingerprint,
        )
        self._fingerprint_verified = True

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Return an initialized HTTP client, creating pinned clients after preflight."""
        await self._ensure_fingerprint()
        if self._client is None:
            self._client = self._new_http_client(
                _build_ssl_context(self._cfg, self._pinned_cert_pem)
            )
        return self._client

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
                    client = await self._ensure_client()
                    resp = await client.request(method, path, **kwargs)
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

    async def get_raw(self, path: str, **kwargs: Any) -> bytes:
        """GET that returns raw bytes (e.g. JPEG screenshots)."""
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            async with self._lock:
                try:
                    client = await self._ensure_client()
                    resp = await client.get(path, **kwargs)
                    resp.raise_for_status()
                    return resp.content
                except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                    last_exc = exc
                    wait = _BACKOFF_BASE * (2**attempt)
                    logger.warning(
                        "raw_request_retry",
                        target=self._cfg.name,
                        path=path,
                        attempt=attempt + 1,
                        wait=wait,
                        error=str(exc),
                    )
            await asyncio.sleep(wait)

        raise ConnectionError(
            f"PiKVM raw request failed after {_MAX_RETRIES} retries: {last_exc}"
        ) from last_exc

    async def stream_sse(self, path: str, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        """POST with SSE streaming response.

        PiKVM uses server-sent events for long-running operations like
        remote MSD writes.  Each event has a JSON data payload with
        progress info.

        Yields dicts parsed from each ``data:`` line.  The final event
        typically has ``"status": "finish"`` or ``"status": "error"``.
        """
        import json

        timeout = kwargs.pop("timeout", 600)
        async with self._lock:
            client = await self._ensure_client()
            async with client.stream(
                "POST",
                path,
                timeout=httpx.Timeout(connect=10.0, read=timeout, write=timeout, pool=10.0),
                **kwargs,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if line.startswith("data:"):
                        payload = line[len("data:") :].strip()
                        if payload:
                            try:
                                yield json.loads(payload)
                            except json.JSONDecodeError:
                                logger.warning(
                                    "sse_parse_error",
                                    target=self._cfg.name,
                                    path=path,
                                    line=payload,
                                )

    async def upload(self, path: str, data: bytes, filename: str) -> dict[str, Any]:
        """Upload binary data (e.g. ISO image) to PiKVM MSD."""
        async with self._lock:
            client = await self._ensure_client()
            resp = await client.post(
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
        if self._client is not None:
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
