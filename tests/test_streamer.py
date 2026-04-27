"""Tests for streamer state + wake-host helper."""

from __future__ import annotations

import pytest
import respx

from pikvm_mcp.client import PiKVMClient
from pikvm_mcp.config import TargetConfig
from pikvm_mcp.tools.streamer import (
    _normalize_streamer_payload,
    streamer_state,
    wake_host,
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


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class TestNormalizeStreamerPayload:
    def test_full_online_payload(self) -> None:
        payload = {
            "ok": True,
            "result": {
                "encoder": {"type": "CPU", "quality": 80},
                "source": {
                    "online": True,
                    "resolution": {"width": 1920, "height": 1080},
                },
                "stream": {"clients_stat": {"client-a": {"fps": 30}}},
            },
        }
        out = _normalize_streamer_payload(payload)
        assert out == {
            "source_online": True,
            "source_resolution": {"w": 1920, "h": 1080},
            "has_clients": True,
            "encoder_type": "CPU",
        }

    def test_offline_minimal_payload(self) -> None:
        payload = {
            "ok": True,
            "result": {
                "encoder": {"type": "CPU"},
                "source": {"online": False},
                "stream": {"clients_stat": {}},
            },
        }
        out = _normalize_streamer_payload(payload)
        assert out["source_online"] is False
        assert out["source_resolution"] == {"w": 0, "h": 0}
        assert out["has_clients"] is False
        assert out["encoder_type"] == "CPU"

    def test_missing_keys_tolerated(self) -> None:
        out = _normalize_streamer_payload({"result": {}})
        assert out == {
            "source_online": False,
            "source_resolution": {"w": 0, "h": 0},
            "has_clients": False,
            "encoder_type": "",
        }

    def test_unwrapped_result(self) -> None:
        # Some callers may pass the inner result directly.
        out = _normalize_streamer_payload(
            {"source": {"online": True, "resolution": {"width": 800, "height": 600}}}
        )
        assert out["source_online"] is True
        assert out["source_resolution"] == {"w": 800, "h": 600}


# ---------------------------------------------------------------------------
# streamer_state via client
# ---------------------------------------------------------------------------


class TestStreamerState:
    async def test_streamer_state_online(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            respx.get(f"{BASE}/api/streamer").respond(
                json={
                    "ok": True,
                    "result": {
                        "encoder": {"type": "CPU"},
                        "source": {
                            "online": True,
                            "resolution": {"width": 2560, "height": 1440},
                        },
                        "stream": {"clients_stat": {}},
                    },
                }
            )
            result = await streamer_state(client)
        assert result["source_online"] is True
        assert result["source_resolution"] == {"w": 2560, "h": 1440}
        assert result["has_clients"] is False
        assert result["encoder_type"] == "CPU"
        await client.close()


# ---------------------------------------------------------------------------
# wake_host
# ---------------------------------------------------------------------------


def _online_response() -> dict:
    return {
        "ok": True,
        "result": {
            "encoder": {"type": "CPU"},
            "source": {"online": True, "resolution": {"width": 1920, "height": 1080}},
            "stream": {"clients_stat": {}},
        },
    }


def _offline_response() -> dict:
    return {
        "ok": True,
        "result": {
            "encoder": {"type": "CPU"},
            "source": {"online": False},
            "stream": {"clients_stat": {}},
        },
    }


async def _noop_sleep(_seconds: float) -> None:
    return None


class TestWakeHost:
    async def test_already_online_skips_events(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            streamer_route = respx.get(f"{BASE}/api/streamer").respond(
                json=_online_response()
            )
            move_route = respx.post(f"{BASE}/api/hid/events/send_mouse_move")
            key_route = respx.post(f"{BASE}/api/hid/events/send_key")

            result = await wake_host(client, sleep=_noop_sleep)

        assert result["woke"] is False
        assert result["reason"] == "already_online"
        assert result["attempts"] == 0
        assert result["state"]["source_online"] is True
        # No events sent because we early-exited.
        assert len(streamer_route.calls) == 1
        assert len(move_route.calls) == 0
        assert len(key_route.calls) == 0
        await client.close()

    async def test_wakes_after_burst(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        # First /api/streamer call: offline. Second (post-burst): online.
        responses = iter([_offline_response(), _online_response()])

        with respx.mock:
            respx.get(f"{BASE}/api/streamer").mock(
                side_effect=lambda request: __import__("httpx").Response(
                    200, json=next(responses)
                )
            )
            move_route = respx.post(
                f"{BASE}/api/hid/events/send_mouse_move"
            ).respond(json={"ok": True})
            key_route = respx.post(f"{BASE}/api/hid/events/send_key").respond(
                json={"ok": True}
            )

            result = await wake_host(
                client,
                poll_interval=0.0,
                timeout=5.0,
                event_interval=0.0,
                sleep=_noop_sleep,
            )

        assert result["woke"] is True
        assert result["attempts"] == 1
        assert result["state"]["source_online"] is True
        # 5 mouse moves with raw HID coords (no calibration / screenshot)
        assert len(move_route.calls) == 5
        for call in move_route.calls:
            url = str(call.request.url)
            assert "to_x=" in url and "to_y=" in url
        # 5 key events, all auto-released (finish=1)
        assert len(key_route.calls) == 5
        for call in key_route.calls:
            url = str(call.request.url)
            assert "finish=1" in url
            assert "state=1" in url
        await client.close()

    async def test_timeout_when_never_online(self, cfg: TargetConfig) -> None:
        client = PiKVMClient(cfg)
        with respx.mock:
            respx.get(f"{BASE}/api/streamer").respond(json=_offline_response())
            respx.post(f"{BASE}/api/hid/events/send_mouse_move").respond(
                json={"ok": True}
            )
            respx.post(f"{BASE}/api/hid/events/send_key").respond(json={"ok": True})

            result = await wake_host(
                client,
                poll_interval=0.0,
                # Tiny timeout — first poll iteration runs, second sees
                # elapsed >= timeout and exits with reason="timeout".
                timeout=0.0001,
                event_interval=0.0,
                sleep=_noop_sleep,
            )

        assert result["woke"] is False
        assert result["reason"] == "timeout"
        assert result["state"]["source_online"] is False
        await client.close()

    async def test_wake_does_not_call_screenshot(self, cfg: TargetConfig) -> None:
        """Critical: raw mouse moves must not trigger calibration screenshots.

        When the streamer is offline, /api/streamer/snapshot returns 503.
        wake_host therefore must use absolute=False so calibrate() is never
        invoked.
        """
        client = PiKVMClient(cfg)
        with respx.mock:
            respx.get(f"{BASE}/api/streamer").respond(json=_offline_response())
            snapshot_route = respx.get(f"{BASE}/api/streamer/snapshot").respond(
                status_code=503
            )
            respx.post(f"{BASE}/api/hid/events/send_mouse_move").respond(
                json={"ok": True}
            )
            respx.post(f"{BASE}/api/hid/events/send_key").respond(json={"ok": True})

            await wake_host(
                client,
                poll_interval=0.0,
                timeout=0.0001,
                event_interval=0.0,
                sleep=_noop_sleep,
            )

        assert len(snapshot_route.calls) == 0
        await client.close()
