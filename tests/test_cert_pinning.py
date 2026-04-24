"""Tests for TLS certificate pinning."""

from __future__ import annotations

import hashlib
import ssl

import pytest

from pikvm_mcp.client import (
    CertificatePinningError,
    _build_ssl_context,
    _normalize_fingerprint,
    _probe_fingerprint,
    _verify_cert_fingerprint,
)
from pikvm_mcp.config import TargetConfig


class TestNormalizeFingerprint:
    def test_colon_separated(self) -> None:
        fp = "AA:BB:CC:DD:EE:FF"
        assert _normalize_fingerprint(fp) == "aabbccddeeff"

    def test_lowercase_passthrough(self) -> None:
        assert _normalize_fingerprint("aabbccdd") == "aabbccdd"

    def test_mixed_case_with_spaces(self) -> None:
        assert _normalize_fingerprint("AA bb CC dd") == "aabbccdd"


class TestBuildSSLContext:
    def test_no_https(self) -> None:
        cfg = TargetConfig(name="t", host="h", https=False)
        assert _build_ssl_context(cfg) is False

    def test_no_verify_no_pin(self) -> None:
        cfg = TargetConfig(name="t", host="h", verify_ssl=False)
        assert _build_ssl_context(cfg) is False

    def test_verify_no_pin(self) -> None:
        cfg = TargetConfig(name="t", host="h", verify_ssl=True)
        assert _build_ssl_context(cfg) is True

    def test_with_fingerprint_requires_pinned_cert(self) -> None:
        cfg = TargetConfig(
            name="t",
            host="h",
            cert_fingerprint="AA:BB:CC:DD",
        )
        with pytest.raises(ValueError, match="Pinned certificate PEM"):
            _build_ssl_context(cfg)

    def test_with_fingerprint_trusts_pinned_cert_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cfg = TargetConfig(
            name="t",
            host="h",
            cert_fingerprint="AA:BB:CC:DD",
        )
        pinned_pem = "-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----"
        calls: dict[str, object] = {}

        class FakeSSLContext:
            check_hostname = True
            verify_mode: ssl.VerifyMode | None = None

        def fake_create_default_context(*, cadata: str | None = None) -> FakeSSLContext:
            calls["cadata"] = cadata
            return FakeSSLContext()

        monkeypatch.setattr(
            "pikvm_mcp.client.ssl.create_default_context",
            fake_create_default_context,
        )

        ctx = _build_ssl_context(cfg, pinned_cert_pem=pinned_pem)
        assert isinstance(ctx, FakeSSLContext)
        assert calls["cadata"] == pinned_pem
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_REQUIRED


class TestVerifyCertFingerprint:
    def test_match_returns_actual_fingerprint(self) -> None:
        der_cert = b"fake-cert-der"
        expected = hashlib.sha256(der_cert).hexdigest()
        assert _verify_cert_fingerprint(der_cert, expected) == expected

    def test_mismatch_raises(self) -> None:
        with pytest.raises(CertificatePinningError, match="fingerprint mismatch"):
            _verify_cert_fingerprint(b"fake-cert-der", "00" * 32)


class TestProbeFingerprint:
    async def test_probe_returns_pinned_cert_pem(self, monkeypatch: pytest.MonkeyPatch) -> None:
        der_cert = b"fake-cert-der"
        expected = hashlib.sha256(der_cert).hexdigest()

        class FakeSSLObject:
            def getpeercert(self, *, binary_form: bool = False) -> bytes:
                assert binary_form is True
                return der_cert

        class FakeWriter:
            def get_extra_info(self, name: str) -> FakeSSLObject | None:
                assert name == "ssl_object"
                return FakeSSLObject()

            def close(self) -> None:
                pass

            async def wait_closed(self) -> None:
                pass

        async def fake_open_connection(host: str, port: int, *, ssl: object) -> tuple[object, FakeWriter]:
            assert host == "pikvm.ts.net"
            assert port == 443
            return object(), FakeWriter()

        monkeypatch.setattr(
            "pikvm_mcp.client.asyncio.open_connection",
            fake_open_connection,
        )

        assert await _probe_fingerprint("pikvm.ts.net", 443, expected) == ssl.DER_cert_to_PEM_cert(
            der_cert
        )


class TestCertificatePinningError:
    def test_is_exception(self) -> None:
        exc = CertificatePinningError("mismatch")
        assert isinstance(exc, Exception)
        assert "mismatch" in str(exc)
