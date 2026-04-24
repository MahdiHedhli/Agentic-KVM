"""Tests for TLS certificate pinning."""

from __future__ import annotations

from pikvm_mcp.client import (
    CertificatePinningError,
    _build_ssl_context,
    _normalize_fingerprint,
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

    def test_with_fingerprint_returns_ssl_context(self) -> None:
        cfg = TargetConfig(
            name="t",
            host="h",
            cert_fingerprint="AA:BB:CC:DD",
        )
        ctx = _build_ssl_context(cfg)
        # Should return an ssl.SSLContext, not bool
        import ssl
        assert isinstance(ctx, ssl.SSLContext)


class TestCertificatePinningError:
    def test_is_exception(self) -> None:
        exc = CertificatePinningError("mismatch")
        assert isinstance(exc, Exception)
        assert "mismatch" in str(exc)
