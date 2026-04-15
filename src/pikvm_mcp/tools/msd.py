"""MSD (Mass Storage Device) tools — the killer feature.

PiKVM MSD lets you present ISO/IMG files to the target machine as a virtual
USB drive or CD-ROM.  This is how you boot from an installer without physical
media.

PiKVM API endpoints used:
    GET  /api/msd         — current MSD state
    POST /api/msd/set_params?image=<name>&cdrom=<0|1>  — select image + mode
    POST /api/msd/set_connected?connected=<0|1>        — plug/unplug virtual drive
    POST /api/msd/write?image=<name>                    — upload image (streamed)
    POST /api/msd/write_remote?url=<url>                — fetch image from URL
"""

from __future__ import annotations

from typing import Any


async def msd_state(client: Any) -> dict[str, Any]:
    """Get current MSD state (mounted image, connected status, storage info)."""
    return await client.get("/api/msd")


async def msd_upload_url(client: Any, *, url: str, timeout: float = 600) -> dict[str, Any]:
    """Tell PiKVM to fetch an image from a URL and store it in MSD storage.

    This is a long-poll operation — PiKVM downloads the image server-side.
    Typical use: point at an ISO URL and wait for it to finish.
    """
    return await client.post(
        "/api/msd/write_remote",
        params={"url": url},
        timeout=timeout,
    )


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
