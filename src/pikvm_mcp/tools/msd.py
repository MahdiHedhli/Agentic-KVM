"""MSD (Mass Storage Device) tools — the killer feature.

PiKVM MSD lets you present ISO/IMG files to the target machine as a virtual
USB drive or CD-ROM.  This is how you boot from an installer without physical
media.

PiKVM API endpoints used:
    GET  /api/msd         — current MSD state
    POST /api/msd/set_params?image=<name>&cdrom=<0|1>  — select image + mode
    POST /api/msd/set_connected?connected=<0|1>        — plug/unplug virtual drive
    POST /api/msd/write?image=<name>                    — upload image (streamed)
    POST /api/msd/write_remote?url=<url>                — fetch image from URL (SSE progress)

The write_remote endpoint streams Server-Sent Events (SSE) with progress
updates during the download.  Each event is a JSON payload::

    {"status": "downloading", "percent": 42, "total": 1073741824, "written": 450887680}
    {"status": "finish"}
    {"status": "error", "error": "..."}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Progress tracking for long-running uploads
# ---------------------------------------------------------------------------


@dataclass
class UploadProgress:
    """Accumulates SSE progress events from a remote MSD write."""

    status: str = "pending"
    percent: int = 0
    total_bytes: int = 0
    written_bytes: int = 0
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def update(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        self.status = event.get("status", self.status)
        self.percent = event.get("percent", self.percent)
        self.total_bytes = event.get("total", self.total_bytes)
        self.written_bytes = event.get("written", self.written_bytes)
        if "error" in event:
            self.error = event["error"]

    @property
    def finished(self) -> bool:
        return self.status in ("finish", "error")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "percent": self.percent,
            "total_bytes": self.total_bytes,
            "written_bytes": self.written_bytes,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# MSD operations
# ---------------------------------------------------------------------------


async def msd_state(client: Any) -> dict[str, Any]:
    """Get current MSD state (mounted image, connected status, storage info)."""
    return await client.get("/api/msd")


async def msd_upload_url(client: Any, *, url: str, timeout: float = 600) -> dict[str, Any]:
    """Tell PiKVM to fetch an image from a URL and store it in MSD storage.

    Streams SSE progress events from PiKVM.  Returns a summary dict with
    the final status, total bytes, and any error.

    Falls back to a plain POST if SSE streaming isn't available (older
    PiKVM firmware).
    """
    progress = UploadProgress()

    try:
        async for event in client.stream_sse(
            "/api/msd/write_remote",
            params={"url": url},
            timeout=timeout,
        ):
            progress.update(event)
            logger.info(
                "msd_upload_progress",
                target=client.target_name,
                status=progress.status,
                percent=progress.percent,
                written=progress.written_bytes,
                total=progress.total_bytes,
            )
            if progress.finished:
                break
    except (AttributeError, NotImplementedError):
        # Fallback: client doesn't support SSE (e.g. mock or older backend)
        result = await client.post(
            "/api/msd/write_remote",
            params={"url": url},
            timeout=timeout,
        )
        return result

    if progress.error:
        raise RuntimeError(f"MSD upload failed: {progress.error}")

    return {
        "ok": True,
        "result": progress.to_dict(),
    }


async def msd_set_image(
    client: Any, *, image: str, cdrom: bool = True
) -> dict[str, Any]:
    """Select which stored image to present and whether it appears as CD-ROM."""
    return await client.post(
        "/api/msd/set_params",
        params={"image": image, "cdrom": int(cdrom)},
    )


async def msd_connect(client: Any) -> dict[str, Any]:
    """Connect (plug in) the virtual drive to the target machine."""
    return await client.post("/api/msd/set_connected", params={"connected": 1})


async def msd_disconnect(client: Any) -> dict[str, Any]:
    """Disconnect (unplug) the virtual drive from the target machine."""
    return await client.post("/api/msd/set_connected", params={"connected": 0})
