"""Tests for HID tools — keyboard, mouse, screenshots, auto-calibration."""

from __future__ import annotations

import struct

import pytest
import respx

from pikvm_mcp.client import PiKVMClient
from pikvm_mcp.config import TargetConfig
from pikvm_mcp.tools.hid import (
    DisplayCalibration,
    _calibrations,
    calibrate,
    detect_resolution_from_jpeg,
    hid_state,
    mouse_click,
    mouse_move,
    mouse_scroll,
    screenshot,
    send_key,
    send_shortcut,
    type_text,
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


def _make_jpeg(width: int = 1920, height: int = 1080) -> bytes:
    """Build a minimal valid JPEG with SOF0 containing the given dimensions.

    This is NOT a displayable image — just enough structure for our JPEG
    dimension parser to extract width/height.
    """
    # JPEG: SOI + SOF0 marker with dimensions + EOI
    sof0 = b"\xff\xc0"  # SOF0 marker
    # SOF0 segment: length(2) + precision(1) + height(2) + width(2) + components...
    # Minimal: 3 components × 3 bytes each = 9 bytes + 8 header = 17
    seg_data = (
        struct.pack(">H", 17)  # segment length including itself
        + b"\x08"  # precision (8 bits)
        + struct.pack(">H", height)
        + struct.pack(">H", width)
        + b"\x03"  # 3 components (YCbCr)
        + b"\x01\x11\x00"  # Y: id=1, sampling=1x1, quant_table=0
        + b"\x02\x11\x01"  # Cb
        + b"\x03\x11\x01"  # Cr
    )
    return b"\xff\xd8" + sof0 + seg_data + b"\xff\xd9"


@pytest.fixture(autouse=True)
def _clear_calibration_cache():
    """Clear the calibration cache between tests."""
    _calibrations.clear()
    yield
    _calibrations.clear()


# ---------------------------------------------------------------------------
# JPEG resolution detection
# ---------------------------------------------------------------------------


class TestJPEGResolution:
    def test_detect_1920x1080(self) -> None:
        w, h = detect_resolution_from_jpeg(_make_jpeg(1920, 1080))
        assert (w, h) == (1920, 1080)

    def test_detect_3840x2160(self) -> None:
        w, h = detect_resolution_from_jpeg(_make_jpeg(3840, 2160))
        assert (w, h) == (3840, 2160)

    def test_detect_1024x768(self) -> None:
        w, h = detect_resolution_from_jpeg(_make_jpeg(1024, 768))
        assert (w, h) == (1024, 768)

    def test_invalid_not_jpeg(self) -> None:
        with pytest.raises(ValueError, match="Not a valid JPEG"):
            detect_resolution_from_jpeg(b"\x00\x00\x00\x00")

    def test_truncated_jpeg(self) -> None:
        with pytest.raises(ValueError):
            detect_resolution_from_jpeg(b"\xff\xd8\xff\xc0\x00")


# ---------------------------------------------------------------------------
# Display calibration
# ---------------------------------------------------------------------------


class TestDisplayCalibration:
    def test_scale_1920x1080(self) -> None:
        cal = DisplayCalibration(width=1920, height=1080)
        assert round(cal.scale_x, 2) == 34.13
        assert round(cal.scale_y, 2) == 60.68

    def test_pixel_to_hid_origin(self) -> None:
        cal = DisplayCalibration(width=1920, height=1080)
        hid_x, hid_y = cal.pixel_to_hid(0, 0)
        assert hid_x == -32768
        assert hid_y == -32768

    def test_pixel_to_hid_center(self) -> None:
        cal = DisplayCalibration(width=1920, height=1080)
        hid_x, hid_y = cal.pixel_to_hid(960, 540)
        assert hid_x in (0, -1)
        assert hid_y in (0, -1)

    def test_pixel_to_hid_max(self) -> None:
        cal = DisplayCalibration(width=1920, height=1080)
        hid_x, hid_y = cal.pixel_to_hid(1920, 1080)
        assert hid_x == 32767
        assert hid_y == 32767


# ---------------------------------------------------------------------------
# Keyboard tools
# ---------------------------------------------------------------------------


class TestKeyboard:
    async def test_hid_state(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            respx.get(f"{BASE}/api/hid").respond(
                json={"ok": True, "result": {"keyboard": {"online": True}}}
            )
            result = await hid_state(client)
        assert result["ok"] is True
        await client.close()

    async def test_type_text(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            route = respx.post(f"{BASE}/api/hid/print").respond(json={"ok": True})
            result = await type_text(client, text="hello world")
            assert result["ok"] is True
            assert route.calls[0].request.content == b"hello world"
        await client.close()

    async def test_send_key(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            route = respx.post(f"{BASE}/api/hid/events/send_key").respond(json={"ok": True})
            result = await send_key(client, key="F12", state=True)
            assert result["ok"] is True
            url = str(route.calls[0].request.url)
            assert "key=F12" in url
            assert "state=1" in url
        await client.close()

    async def test_send_shortcut(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            route = respx.post(f"{BASE}/api/hid/events/send_shortcut").respond(
                json={"ok": True}
            )
            result = await send_shortcut(
                client, keys=["ControlLeft", "AltLeft", "Delete"], hold_ms=10
            )
            assert result["ok"] is True
            assert "keys=ControlLeft%2CAltLeft%2CDelete" in str(route.calls[0].request.url)
        await client.close()


# ---------------------------------------------------------------------------
# Mouse tools
# ---------------------------------------------------------------------------


class TestMouse:
    async def test_mouse_move_absolute(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            # First call: screenshot for calibration
            respx.get(f"{BASE}/api/streamer/snapshot").respond(
                content=_make_jpeg(1920, 1080),
                headers={"content-type": "image/jpeg"},
            )
            # Second call: mouse event
            route = respx.post(f"{BASE}/api/hid/events/send_mouse_move").respond(
                json={"ok": True}
            )

            result = await mouse_move(client, x=960, y=540, absolute=True)
            assert result["ok"] is True
            url = str(route.calls[0].request.url)
            assert "to_x=" in url
            assert "to_y=" in url
        await client.close()

    async def test_mouse_move_raw(self, cfg: TargetConfig) -> None:
        """Raw mode should NOT trigger calibration (no screenshot)."""
        client = PiKVMClient(cfg)
        with respx.mock:
            route = respx.post(f"{BASE}/api/hid/events/send_mouse_move").respond(
                json={"ok": True}
            )
            result = await mouse_move(client, x=32767, y=32767, absolute=False)
            assert result["ok"] is True
            assert "to_x=32767" in str(route.calls[0].request.url)
        await client.close()

    async def test_mouse_click_at_position(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            respx.get(f"{BASE}/api/streamer/snapshot").respond(
                content=_make_jpeg(1920, 1080),
                headers={"content-type": "image/jpeg"},
            )
            move_route = respx.post(f"{BASE}/api/hid/events/send_mouse_move").respond(
                json={"ok": True}
            )
            button_route = respx.post(f"{BASE}/api/hid/events/send_mouse_button").respond(
                json={"ok": True}
            )

            result = await mouse_click(client, button="left", x=100, y=200)
            assert result["ok"] is True
            assert len(move_route.calls) == 1
            assert len(button_route.calls) == 2
        await client.close()

    async def test_mouse_click_no_coords(self, cfg: TargetConfig) -> None:
        """Click without coordinates should not trigger calibration."""
        client = PiKVMClient(cfg)
        with respx.mock:
            route = respx.post(f"{BASE}/api/hid/events/send_mouse_button").respond(
                json={"ok": True}
            )
            result = await mouse_click(client, button="right")
            assert result["ok"] is True
            assert len(route.calls) == 2
        await client.close()

    async def test_mouse_scroll(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            route = respx.post(f"{BASE}/api/hid/events/send_mouse_wheel").respond(
                json={"ok": True}
            )
            result = await mouse_scroll(client, delta_y=-3)
            assert result["ok"] is True
            assert "delta_y=-3" in str(route.calls[0].request.url)
        await client.close()


# ---------------------------------------------------------------------------
# Screenshot + auto-calibration
# ---------------------------------------------------------------------------


class TestScreenshot:
    async def test_screenshot_returns_jpeg(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        jpeg = _make_jpeg(1920, 1080)
        with respx.mock:
            respx.get(f"{BASE}/api/streamer/snapshot").respond(
                content=jpeg,
                headers={"content-type": "image/jpeg"},
            )
            data = await screenshot(client)
        assert data == jpeg
        await client.close()

    async def test_screenshot_triggers_calibration(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            respx.get(f"{BASE}/api/streamer/snapshot").respond(
                content=_make_jpeg(2560, 1440),
                headers={"content-type": "image/jpeg"},
            )
            await screenshot(client)
        assert "test-kvm" in _calibrations
        assert _calibrations["test-kvm"].width == 2560
        assert _calibrations["test-kvm"].height == 1440
        await client.close()

    async def test_calibrate_caches(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            route = respx.get(f"{BASE}/api/streamer/snapshot").respond(
                content=_make_jpeg(1920, 1080),
                headers={"content-type": "image/jpeg"},
            )
            cal1 = await calibrate(client)
            cal2 = await calibrate(client)
            assert cal1 is cal2
            # Should only screenshot once (cached)
            assert len(route.calls) == 1
        await client.close()
