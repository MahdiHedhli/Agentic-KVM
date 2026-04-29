"""Supermicro legacy web iKVM / virtual-media tools."""

from __future__ import annotations

from typing import Any


async def vm_status(client: Any) -> dict[str, Any]:
    """Return legacy virtual-media device status from the Supermicro web UI."""
    return await client.vm_status()


async def vm_config_get(client: Any) -> dict[str, Any]:
    """Return the currently configured legacy virtual-media source."""
    return await client.vm_config_get()


async def vm_config_set(
    client: Any,
    *,
    host: str,
    path: str,
    user: str = "",
    password: str = "",
) -> dict[str, Any]:
    """Set the legacy virtual-media source.

    For HTTP/HTTPS, ``host`` is the base URL like ``https://10.0.0.10:8080`` and
    ``path`` is the ISO path like ``/isos/alpine.iso``.

    For SMB, ``host`` is the share host and ``path`` is typically a UNC-like path
    such as ``\\\\share\\folder\\image.iso``.
    """
    return await client.vm_config_set(host=host, path=path, user=user, password=password)


async def vm_mount(client: Any) -> dict[str, Any]:
    """Mount the configured virtual-media image into the target host."""
    return await client.vm_mount()


async def vm_unmount(client: Any) -> dict[str, Any]:
    """Unmount the currently mounted virtual-media image."""
    return await client.vm_unmount()


async def ikvm_jnlp(client: Any) -> dict[str, Any]:
    """Fetch and parse the authenticated ATEN/Supermicro iKVM JNLP descriptor."""
    return await client.ikvm_jnlp()
