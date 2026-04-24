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

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

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


def _image_name_from_url(url: str) -> str:
    """Return the image filename PiKVM will derive from a remote URL."""
    name = PurePosixPath(unquote(urlparse(url).path)).name
    if not name:
        raise ValueError(f"Could not derive image name from URL: {url}")
    return name


async def _wait_for_remote_image(
    client: Any,
    *,
    image: str,
    timeout: float,
) -> dict[str, Any]:
    """Poll MSD state until a remotely fetched image appears complete."""
    deadline = time.monotonic() + timeout
    while True:
        state = await msd_state(client)
        storage = state["result"]["storage"]
        image_info = storage["images"].get(image)
        if image_info and image_info.get("complete", True):
            size = image_info.get("size", 0)
            return {
                "status": "finish",
                "percent": 100,
                "total_bytes": size,
                "written_bytes": size,
                "error": None,
                "image": image,
            }

        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for MSD image to finish: {image}")

        await asyncio.sleep(1)


async def msd_upload_url(client: Any, *, url: str, timeout: float = 600) -> dict[str, Any]:
    """Tell PiKVM to fetch an image from a URL and store it in MSD storage.

    Streams SSE progress events from PiKVM.  Returns a summary dict with
    the final status, total bytes, and any error.

    Falls back to a plain POST if SSE streaming isn't available (older
    PiKVM firmware).
    """
    progress = UploadProgress()
    image = _image_name_from_url(url)

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
        await client.post(
            "/api/msd/write_remote",
            params={"url": url},
            timeout=timeout,
        )
        return {
            "ok": True,
            "result": await _wait_for_remote_image(client, image=image, timeout=timeout),
        }

    if progress.error:
        raise RuntimeError(f"MSD upload failed: {progress.error}")

    if not progress.finished:
        return {
            "ok": True,
            "result": await _wait_for_remote_image(client, image=image, timeout=timeout),
        }

    result = progress.to_dict()
    result["image"] = image
    return {
        "ok": True,
        "result": result,
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
    state = await msd_state(client)
    if state["result"]["drive"]["connected"]:
        return {
            "ok": True,
            "result": {"connected": True, "already_connected": True},
        }
    return await client.post("/api/msd/set_connected", params={"connected": 1})


async def msd_disconnect(client: Any) -> dict[str, Any]:
    """Disconnect (unplug) the virtual drive from the target machine."""
    state = await msd_state(client)
    if not state["result"]["drive"]["connected"]:
        return {
            "ok": True,
            "result": {"connected": False, "already_disconnected": True},
        }
    return await client.post("/api/msd/set_connected", params={"connected": 0})
