"""Tests for local bridge helpers."""

from __future__ import annotations

from pathlib import Path

import pikvm_mcp.bridge as bridge
from pikvm_mcp.supermicro_client import JnlpLaunchInfo


def _jnlp() -> JnlpLaunchInfo:
    return JnlpLaunchInfo(
        title="ATEN Java iKVM Viewer",
        vendor="ATEN",
        codebase="http://bmc:80/",
        main_class="tw.com.aten.ikvm.KVMMain",
        jar_href="iKVM.jar",
        arguments=["bmc", "token"],
        raw_jnlp='<jnlp><resources><j2se version="1.6+" java-vm-args="-Xms128m -XX:PermSize=32M -XX:MaxPermSize=128M"/></resources></jnlp>',
    )


def test_prepare_supermicro_ikvm_bundle(tmp_path: Path) -> None:
    bundle = bridge.prepare_supermicro_ikvm_bundle(
        runtime_dir=tmp_path,
        target_name="sm lab",
        jnlp=_jnlp(),
    )

    assert bundle.bundle_dir.exists()
    rendered_jnlp = bundle.jnlp_path.read_text(encoding="utf-8")
    assert "-XX:PermSize" not in rendered_jnlp
    assert "-XX:MaxPermSize" not in rendered_jnlp
    assert "-Xms128m" in rendered_jnlp
    manifest = bundle.manifest_path.read_text(encoding="utf-8")
    assert '"target_name": "sm lab"' in manifest
    assert "launch-bridge.sh" in bundle.docker_script_path.name
    assert bundle.log_dir.exists()


def test_launch_supermicro_bridge_uses_docker(monkeypatch, tmp_path: Path) -> None:
    bundle = bridge.prepare_supermicro_ikvm_bundle(
        runtime_dir=tmp_path,
        target_name="sm lab",
        jnlp=_jnlp(),
    )
    calls: list[list[str]] = []

    class Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd, capture_output=True, text=True, check=False):  # noqa: ANN001
        calls.append(list(cmd))
        if cmd[:3] == ["docker", "image", "inspect"]:
            return Result(0, stdout="[]")
        if cmd[:3] == ["docker", "inspect", "-f"]:
            return Result(0, stdout="running\n")
        if cmd[:2] == ["docker", "run"]:
            return Result(0, stdout="container-123\n")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    monkeypatch.setattr(bridge, "_find_free_port", lambda host="127.0.0.1": 6123)

    result = bridge.launch_supermicro_bridge(
        bundle=bundle,
        repo_root=tmp_path,
        port=None,
    )

    assert result["container_name"].startswith("agentic-kvm-ikvm-sm-lab-")
    assert result["url"] == "http://127.0.0.1:6123/vnc.html"
    assert result["log_dir"]
    assert any(cmd[:2] == ["docker", "run"] for cmd in calls)


def test_stop_supermicro_bridge(monkeypatch) -> None:
    class Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    monkeypatch.setattr(
        bridge.subprocess,
        "run",
        lambda *args, **kwargs: Result(0, stdout="bridge-1\n"),
    )

    result = bridge.stop_supermicro_bridge("bridge-1")
    assert result == {"container_name": "bridge-1", "stopped": True}
