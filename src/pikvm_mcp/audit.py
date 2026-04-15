"""Session recorder middleware — structured JSONL audit trail.

Every tool invocation is logged with::

    {
      "ts":          "2025-04-15T12:34:56.789Z",
      "target_id":   "lab-server",
      "operator_id": "operator@redteam",
      "tool":        "pikvm_atx_power_on",
      "args":        {"target": "lab-server"},
      "result":      "ok",
      "duration_ms": 342,
      "session_id":  "a1b2c3d4"
    }

The audit directory is configured via ``PIKVM_AUDIT_DIR`` (default
``/var/log/pikvm-mcp``).  One file per session: ``session-<id>.jsonl``.

This is the chain-of-custody log the red team product depends on.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Coroutine
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


class SessionRecorder:
    """Append-only JSONL audit logger."""

    def __init__(self, audit_dir: Path, operator_id: str) -> None:
        self.session_id = uuid.uuid4().hex[:8]
        self.operator_id = operator_id
        self._audit_dir = audit_dir
        self._audit_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._audit_dir / f"session-{self.session_id}.jsonl"
        self._file = open(self._path, "a", encoding="utf-8")  # noqa: SIM115
        logger.info("audit_session_started", session_id=self.session_id, path=str(self._path))

    def record(
        self,
        *,
        target_id: str,
        tool: str,
        args: dict[str, Any],
        result: str,
        duration_ms: float,
    ) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            "target_id": target_id,
            "operator_id": self.operator_id,
            "tool": tool,
            "args": _sanitize_args(args),
            "result": result,
            "duration_ms": round(duration_ms, 1),
        }
        self._file.write(json.dumps(entry) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()
        logger.info("audit_session_closed", session_id=self.session_id)


def _sanitize_args(args: dict[str, Any]) -> dict[str, Any]:
    """Strip secrets from logged arguments."""
    sanitized = {}
    for k, v in args.items():
        if any(secret in k.lower() for secret in ("password", "secret", "token", "otp")):
            sanitized[k] = "***"
        else:
            sanitized[k] = v
    return sanitized


# ---------------------------------------------------------------------------
# Decorator for tool functions
# ---------------------------------------------------------------------------


def audited(
    recorder: SessionRecorder,
    resolve_target: Callable[..., str],
) -> Callable:
    """Decorator that wraps an async tool function with audit logging.

    ``resolve_target`` is called with the tool's kwargs to determine the
    target_id for the log entry.
    """

    def decorator(
        fn: Callable[..., Coroutine[Any, Any, Any]],
    ) -> Callable[..., Coroutine[Any, Any, Any]]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            target_id = resolve_target(**kwargs)
            t0 = time.monotonic()
            result_summary = "ok"
            try:
                result = await fn(*args, **kwargs)
                return result
            except Exception as exc:
                result_summary = f"error: {type(exc).__name__}: {exc}"
                raise
            finally:
                duration_ms = (time.monotonic() - t0) * 1000
                recorder.record(
                    target_id=target_id,
                    tool=fn.__name__,
                    args=kwargs,
                    result=result_summary,
                    duration_ms=duration_ms,
                )

        return wrapper

    return decorator
