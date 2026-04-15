"""Tests for the session recorder / audit trail."""

from __future__ import annotations

import json
from pathlib import Path

from pikvm_mcp.audit import SessionRecorder, _sanitize_args


class TestSessionRecorder:
    def test_creates_session_file(self, tmp_path: Path) -> None:
        recorder = SessionRecorder(audit_dir=tmp_path, operator_id="test-op")
        assert (tmp_path / f"session-{recorder.session_id}.jsonl").exists()
        recorder.close()

    def test_records_entry(self, tmp_path: Path) -> None:
        recorder = SessionRecorder(audit_dir=tmp_path, operator_id="test-op")
        recorder.record(
            target_id="test-kvm",
            tool="pikvm_atx_power_on",
            args={"target": "test-kvm"},
            result="ok",
            duration_ms=42.5,
        )
        recorder.close()

        logfile = tmp_path / f"session-{recorder.session_id}.jsonl"
        lines = logfile.read_text().strip().split("\n")
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["target_id"] == "test-kvm"
        assert entry["operator_id"] == "test-op"
        assert entry["tool"] == "pikvm_atx_power_on"
        assert entry["result"] == "ok"
        assert entry["duration_ms"] == 42.5
        assert "ts" in entry
        assert "session_id" in entry

    def test_multiple_entries(self, tmp_path: Path) -> None:
        recorder = SessionRecorder(audit_dir=tmp_path, operator_id="test-op")
        for i in range(5):
            recorder.record(
                target_id="test-kvm",
                tool=f"tool_{i}",
                args={},
                result="ok",
                duration_ms=float(i),
            )
        recorder.close()

        logfile = tmp_path / f"session-{recorder.session_id}.jsonl"
        lines = logfile.read_text().strip().split("\n")
        assert len(lines) == 5


class TestSanitizeArgs:
    def test_strips_password(self) -> None:
        assert _sanitize_args({"password": "hunter2"}) == {"password": "***"}

    def test_strips_secret(self) -> None:
        assert _sanitize_args({"otp_secret": "JBSWY3DPEHPK3PXP"}) == {"otp_secret": "***"}

    def test_preserves_normal_args(self) -> None:
        args = {"target": "lab-server", "url": "https://example.com/image.iso"}
        assert _sanitize_args(args) == args
