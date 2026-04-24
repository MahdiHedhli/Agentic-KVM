"""Multi-target configuration with Pydantic validation.

Supports a list of named PiKVM targets.  One is marked active (or the first
is used by default).  Every tool call takes an optional ``target`` parameter
that resolves through this config.

Environment variable layout (flat, Docker-friendly)::

    PIKVM_TARGETS='[{"name":"lab-server","host":"pikvm-lab.ts.net",...}]'
    PIKVM_DEFAULT_TARGET=lab-server
    PIKVM_AUDIT_DIR=/var/log/pikvm-mcp
    PIKVM_OPERATOR_ID=operator@redteam
    PIKVM_FULL_CAPTURE=false
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Self

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TargetConfig(BaseModel):
    """Connection details for a single PiKVM instance."""

    name: str = Field(description="Human-readable target identifier (e.g. 'lab-server')")
    host: str = Field(description="Hostname or IP, ideally a Tailscale MagicDNS name")
    port: int = Field(default=443)
    https: bool = Field(default=True)
    username: str = Field(default="admin")
    password: SecretStr = Field(default=SecretStr("admin"))
    otp_secret: SecretStr | None = Field(
        default=None,
        description="TOTP secret for 2FA-enabled PiKVM instances",
    )
    cert_fingerprint: str | None = Field(
        default=None,
        description="SHA-256 fingerprint for certificate pinning (hex, colon-separated)",
    )
    verify_ssl: bool = Field(
        default=False,
        description="Verify TLS cert. False is sane default for self-signed PiKVM certs.",
    )

    @property
    def base_url(self) -> str:
        scheme = "https" if self.https else "http"
        return f"{scheme}://{self.host}:{self.port}"


class AppConfig(BaseSettings):
    """Top-level application settings, loaded from env vars."""

    model_config = SettingsConfigDict(
        env_prefix="PIKVM_",
        env_nested_delimiter="__",
    )

    targets_json: str = Field(
        default="[]",
        alias="PIKVM_TARGETS",
        description="JSON array of TargetConfig objects",
    )
    default_target: str | None = Field(
        default=None,
        alias="PIKVM_DEFAULT_TARGET",
        description="Name of the default target",
    )
    audit_dir: Path = Field(
        default=Path("/var/log/pikvm-mcp"),
        alias="PIKVM_AUDIT_DIR",
    )
    operator_id: str = Field(
        default="unknown",
        alias="PIKVM_OPERATOR_ID",
    )
    full_capture: bool = Field(
        default=False,
        alias="PIKVM_FULL_CAPTURE",
        description="Log sensitive operator-entered HID text for explicit full-capture sessions",
    )

    # --- Parsed from JSON at validation time ---
    targets: list[TargetConfig] = Field(default_factory=list, exclude=True)

    @model_validator(mode="after")
    def _parse_targets_json(self) -> Self:
        if self.targets_json and self.targets_json != "[]":
            raw = json.loads(self.targets_json)
            self.targets = [TargetConfig(**t) for t in raw]
        return self

    def resolve_target(self, name: str | None = None) -> TargetConfig:
        """Return the named target, or the default, or the first configured."""
        if not self.targets:
            raise ValueError(
                "No PiKVM targets configured. Set PIKVM_TARGETS env var."
            )
        if name:
            for t in self.targets:
                if t.name == name:
                    return t
            raise ValueError(
                f"Target '{name}' not found. Available: {[t.name for t in self.targets]}"
            )
        if self.default_target:
            return self.resolve_target(self.default_target)
        return self.targets[0]


def load_env_file_from_environment() -> Path | None:
    """Load the optional dotenv file pointed to by PIKVM_ENV_FILE.

    MCP clients often support passing a small ``env`` map but should not carry
    PiKVM passwords directly in their config.  ``PIKVM_ENV_FILE`` lets local
    dogfood clients point at an ignored .env file while production deployments
    continue to use normal environment variables.

    Existing process environment variables win over values from the file.
    """
    path = os.environ.get("PIKVM_ENV_FILE")
    if not path:
        return None

    env_path = Path(path).expanduser()
    if not env_path.exists():
        raise FileNotFoundError(f"PIKVM_ENV_FILE does not exist: {env_path}")

    load_dotenv(env_path, override=False)
    return env_path
