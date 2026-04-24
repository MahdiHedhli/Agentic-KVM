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
import inspect
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

    def __init__(self, audit_dir: Path, operator_id: str, full_capture: bool = False) -> None:
        self.session_id = uuid.uuid4().hex[:8]
        self.operator_id = operator_id
        self.full_capture = full_capture
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
            "args": _sanitize_args(args, full_capture=self.full_capture),
            "result": result,
            "duration_ms": round(duration_ms, 1),
        }
        self._file.write(json.dumps(entry) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()
        logger.info("audit_session_closed", session_id=self.session_id)


def _sanitize_args(args: dict[str, Any], *, full_capture: bool = False) -> dict[str, Any]:
    """Strip secrets and internal objects from logged arguments.

    ``full_capture`` permits user-entered HID text in audit logs for explicit
    engagement recording, but still redacts credential-like fields.
    """
    sanitized = {}
    for k, v in args.items():
        if k == "client":
            continue
        sanitized[k] = _sanitize_value(k, v, full_capture=full_capture)
    return sanitized


def _sanitize_value(key: str, value: Any, *, full_capture: bool) -> Any:
    """Return a redacted, JSON-serializable representation for audit logs."""
    key_lower = key.lower()
    if any(secret in key_lower for secret in ("password", "secret", "token", "otp")):
        return "***"
    if key == "text" and not full_capture:
        return "***"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_sanitize_value("", item, full_capture=full_capture) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_value("", item, full_capture=full_capture) for item in value]
    if isinstance(value, dict):
        return {
            str(k): _sanitize_value(str(k), v, full_capture=full_capture)
            for k, v in value.items()
        }
    return repr(value)


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
        signature = inspect.signature(fn)
        accepts_kwargs = any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        )
        accepted_kwargs = {
            name
            for name, param in signature.parameters.items()
            if param.kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }

        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            target_id = resolve_target(**kwargs)
            t0 = time.monotonic()
            result_summary = "ok"
            try:
                call_kwargs = kwargs
                if not accepts_kwargs:
                    call_kwargs = {
                        key: value
                        for key, value in kwargs.items()
                        if key in accepted_kwargs
                    }
                result = await fn(*args, **call_kwargs)
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
