"""Local launcher/bridge helpers for vendor KVM runtimes."""

from __future__ import annotations

import json
import os
import random
import re
import socket
import string
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from pikvm_mcp.supermicro_client import JnlpLaunchInfo

_IMAGE_TAG = "agentic-kvm/supermicro-ikvm-bridge:latest"


class BridgeError(Exception):
    """Raised when local bridge preparation or launch fails."""


def _slug(value: str) -> str:
    out = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return out or "target"


def _rand(n: int = 6) -> str:
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def _find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _sanitize_java_vm_args(raw_jnlp: str) -> str:
    """Strip legacy VM flags that modern OpenJDK rejects.

    Supermicro's ATEN JNLP commonly ships `-XX:PermSize` / `-XX:MaxPermSize`
    flags that were removed after Java 8. IcedTea-Web on modern JREs passes
    those through verbatim, which prevents the viewer from starting at all.
    """
    root = ET.fromstring(raw_jnlp)
    for elem in root.iter():
        vm_args = elem.attrib.get("java-vm-args")
        if not vm_args:
            continue
        kept: list[str] = []
        for token in vm_args.split():
            if token.startswith("-XX:PermSize=") or token.startswith("-XX:MaxPermSize="):
                continue
            kept.append(token)
        if kept:
            elem.attrib["java-vm-args"] = " ".join(kept)
        else:
            del elem.attrib["java-vm-args"]
    return ET.tostring(root, encoding="unicode")


@dataclass(slots=True)
class BridgeBundle:
    target_name: str
    session_id: str
    bundle_dir: Path
    jnlp_path: Path
    manifest_path: Path
    launch_script_path: Path
    docker_script_path: Path
    log_dir: Path


def prepare_supermicro_ikvm_bundle(
    *,
    runtime_dir: Path,
    target_name: str,
    jnlp: JnlpLaunchInfo,
) -> BridgeBundle:
    session_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{_rand()}"
    bundle_dir = runtime_dir / "supermicro-ikvm" / _slug(target_name) / session_id
    bundle_dir.mkdir(parents=True, exist_ok=False)

    jnlp_path = bundle_dir / "ikvm.jnlp"
    manifest_path = bundle_dir / "manifest.json"
    launch_script_path = bundle_dir / "launch-javaws.sh"
    docker_script_path = bundle_dir / "launch-bridge.sh"
    log_dir = bundle_dir / "bridge-logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    jnlp_path.write_text(_sanitize_java_vm_args(jnlp.raw_jnlp), encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "target_name": target_name,
                "session_id": session_id,
                "created_at": datetime.now(UTC).isoformat(),
                "title": jnlp.title,
                "vendor": jnlp.vendor,
                "codebase": jnlp.codebase,
                "main_class": jnlp.main_class,
                "jar_href": jnlp.jar_href,
                "arguments": jnlp.arguments,
                "warning": "This bundle contains authenticated session tokens. Treat it as sensitive.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    launch_script_path.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                "set -eu",
                'if command -v javaws >/dev/null 2>&1; then',
                '  exec javaws "$0.dir/ikvm.jnlp"',
                "fi",
                'echo "javaws is not available on this host." >&2',
                "exit 1",
                "",
            ]
        ).replace("$0.dir", str(bundle_dir)),
        encoding="utf-8",
    )
    docker_script_path.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                "set -eu",
                'PORT="${1:-6080}"',
                f'exec docker run -d --name "agentic-kvm-ikvm-{_slug(target_name)}-{session_id}" \\',
                '  -p "127.0.0.1:${PORT}:6080" \\',
                f'  -v "{bundle_dir}:/session" \\',
                f"  {_IMAGE_TAG}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    os.chmod(launch_script_path, 0o700)
    os.chmod(docker_script_path, 0o700)

    return BridgeBundle(
        target_name=target_name,
        session_id=session_id,
        bundle_dir=bundle_dir,
        jnlp_path=jnlp_path,
        manifest_path=manifest_path,
        launch_script_path=launch_script_path,
        docker_script_path=docker_script_path,
        log_dir=log_dir,
    )


def ensure_supermicro_bridge_image(repo_root: Path) -> dict[str, Any]:
    inspect = subprocess.run(
        ["docker", "image", "inspect", _IMAGE_TAG],
        capture_output=True,
        text=True,
        check=False,
    )
    if inspect.returncode == 0:
        return {"image_tag": _IMAGE_TAG, "built": False}

    dockerfile = repo_root / "docker" / "supermicro-ikvm-bridge" / "Dockerfile"
    if not dockerfile.exists():
        raise BridgeError(f"Bridge Dockerfile is missing: {dockerfile}")
    build = subprocess.run(
        [
            "docker",
            "build",
            "-t",
            _IMAGE_TAG,
            "-f",
            str(dockerfile),
            str(repo_root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        raise BridgeError(build.stderr.strip() or build.stdout.strip() or "docker build failed")
    return {"image_tag": _IMAGE_TAG, "built": True}


def launch_supermicro_bridge(
    *,
    bundle: BridgeBundle,
    repo_root: Path,
    port: int | None = None,
) -> dict[str, Any]:
    image = ensure_supermicro_bridge_image(repo_root)
    host_port = port or _find_free_port()
    container_name = f"agentic-kvm-ikvm-{_slug(bundle.target_name)}-{bundle.session_id}"

    run = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "-p",
            f"127.0.0.1:{host_port}:6080",
            "-v",
            f"{bundle.bundle_dir}:/session",
            image["image_tag"],
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if run.returncode != 0:
        raise BridgeError(run.stderr.strip() or run.stdout.strip() or "docker run failed")
    container_id = run.stdout.strip()
    inspect = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Status}}", container_name],
        capture_output=True,
        text=True,
        check=False,
    )
    status = inspect.stdout.strip() if inspect.returncode == 0 else "unknown"
    if status != "running":
        logs = subprocess.run(
            ["docker", "logs", container_name],
            capture_output=True,
            text=True,
            check=False,
        )
        raise BridgeError(
            f"bridge container exited immediately (status={status}): "
            f"{(logs.stderr or logs.stdout).strip()}"
        )
    return {
        "container_name": container_name,
        "container_id": container_id,
        "image_tag": image["image_tag"],
        "image_built": image["built"],
        "bundle_dir": str(bundle.bundle_dir),
        "log_dir": str(bundle.log_dir),
        "url": f"http://127.0.0.1:{host_port}/vnc.html",
        "port": host_port,
    }


def stop_supermicro_bridge(container_name: str) -> dict[str, Any]:
    stop = subprocess.run(
        ["docker", "rm", "-f", container_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if stop.returncode != 0:
        raise BridgeError(stop.stderr.strip() or stop.stdout.strip() or "docker rm -f failed")
    return {"container_name": container_name, "stopped": True}
