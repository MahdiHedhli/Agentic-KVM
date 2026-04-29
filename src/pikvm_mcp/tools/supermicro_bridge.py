"""Local Supermicro iKVM bridge helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pikvm_mcp.bridge import (
    launch_supermicro_bridge,
    prepare_supermicro_ikvm_bundle,
    stop_supermicro_bridge,
)


async def prepare_ikvm_bundle(
    client: Any,
    *,
    runtime_dir: Path,
) -> dict[str, Any]:
    """Fetch the authenticated JNLP and write a local bundle."""
    jnlp = await client.ikvm_jnlp_info()
    bundle = prepare_supermicro_ikvm_bundle(
        runtime_dir=runtime_dir,
        target_name=client.target_name,
        jnlp=jnlp,
    )
    return {
        "target_name": client.target_name,
        "session_id": bundle.session_id,
        "bundle_dir": str(bundle.bundle_dir),
        "jnlp_path": str(bundle.jnlp_path),
        "manifest_path": str(bundle.manifest_path),
        "launch_script_path": str(bundle.launch_script_path),
        "docker_script_path": str(bundle.docker_script_path),
    }


async def launch_ikvm_bridge(
    client: Any,
    *,
    runtime_dir: Path,
    repo_root: Path,
    port: int | None = None,
) -> dict[str, Any]:
    """Fetch JNLP, materialize a local bundle, and launch the Docker noVNC bridge."""
    jnlp = await client.ikvm_jnlp_info()
    bundle = prepare_supermicro_ikvm_bundle(
        runtime_dir=runtime_dir,
        target_name=client.target_name,
        jnlp=jnlp,
    )
    launched = launch_supermicro_bridge(bundle=bundle, repo_root=repo_root, port=port)
    launched["session_id"] = bundle.session_id
    return launched


async def stop_ikvm_bridge(container_name: str) -> dict[str, Any]:
    """Stop a previously launched Docker-based Supermicro iKVM bridge."""
    return stop_supermicro_bridge(container_name)
