# Agentic-KVM

**FastMCP server giving AI agents bare-metal control of remote machines via PiKVM.**

> **Status:** v0.1 foundation. This is the open-source core of a commercial
> red team appliance and a cloud-hosted computer-use-as-a-service product.
> It works, it's tested, it's not feature-complete. Contributions welcome.

## What it does

Agentic-KVM exposes PiKVM's REST API as MCP tools that any AI agent can call:

- **MSD** (Mass Storage Device) — Upload ISOs, mount virtual drives, boot from installers
- **ATX** — Power on, power off, hard reset via motherboard header pins
- **HID** — Keyboard, mouse, screenshots, and screenshot-based mouse calibration
- **Audit trail** — Every tool call logged to JSONL for chain-of-custody

## Architecture

```
┌──────────────┐     stdio/MCP      ┌──────────────────┐    HTTPS     ┌─────────┐
│  AI Agent    │◄──────────────────►│  Agentic-KVM     │◄───────────►│  PiKVM  │
│  (Claude,    │                    │  (FastMCP server) │  Tailscale  │  Device │
│   etc.)      │                    │                   │             │         │
└──────────────┘                    └──────────────────┘             └─────────┘
                                           │
                                           ▼
                                    /var/log/pikvm-mcp/
                                    session-<id>.jsonl
```

The PiKVM client sits behind a `TargetBackend` abstraction.  Future backends
(Redfish BMC, Azure VM, RDP) will implement the same interface so the tool
layer doesn't change.

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for environment management
- A PiKVM device reachable on your network (Tailscale recommended)

### Install and run locally

```bash
uv sync
export PIKVM_TARGETS='[{"name":"my-kvm","host":"pikvm.ts.net","password":"your-password"}]'
export PIKVM_OPERATOR_ID="your-name"
uv run pikvm-mcp
```

### Docker (recommended for production)

```bash
docker build -t pikvm-mcp .

docker run -d --name pikvm-mcp \
  -e PIKVM_TARGETS='[{"name":"lab","host":"pikvm-lab.ts.net","password":"..."}]' \
  -e PIKVM_OPERATOR_ID=operator@redteam \
  -v pikvm-mcp-audit:/var/log/pikvm-mcp \
  pikvm-mcp

# Connect an MCP client
docker exec -i pikvm-mcp pikvm-mcp
```

### Claude Desktop / MCP client config

```json
{
  "mcpServers": {
    "pikvm": {
      "command": "docker",
      "args": ["exec", "-i", "pikvm-mcp", "pikvm-mcp"]
    }
  }
}
```

### Local MCP Client Config

For local dogfood, keep secrets in the ignored `.env` file and point the MCP
client at that file with `PIKVM_ENV_FILE`. This avoids copying PiKVM passwords
into client config files.

```json
{
  "mcpServers": {
    "agentic-kvm": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/Agentic-KVM",
        "run",
        "python",
        "-m",
        "pikvm_mcp.server"
      ],
      "env": {
        "PYTHONPATH": "/path/to/Agentic-KVM/src",
        "PIKVM_ENV_FILE": "/path/to/Agentic-KVM/.env"
      }
    }
  }
}
```

Replace `/path/to/Agentic-KVM` with your local checkout path.

Production deployments should continue to pass real environment variables
directly through the container, process supervisor, or secret manager.

## Multi-Target Configuration

Agentic-KVM supports multiple PiKVM targets from day one.  Every tool accepts
an optional `target` parameter.

```bash
export PIKVM_TARGETS='[
  {"name": "lab-server",  "host": "pikvm-lab.ts.net",  "password": "..."},
  {"name": "prod-server", "host": "pikvm-prod.ts.net", "password": "...", "port": 8443}
]'
export PIKVM_DEFAULT_TARGET=lab-server
```

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `PIKVM_TARGETS` | JSON array of target configs | `[]` |
| `PIKVM_DEFAULT_TARGET` | Name of the default target | First in list |
| `PIKVM_OPERATOR_ID` | Operator ID for audit logs | `unknown` |
| `PIKVM_AUDIT_DIR` | Directory for JSONL audit logs | `/var/log/pikvm-mcp` |
| `PIKVM_FULL_CAPTURE` | Include operator-entered HID text in audit logs | `false` |

### Target config fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | required | Human-readable identifier |
| `host` | string | required | Hostname or IP (Tailscale MagicDNS recommended) |
| `port` | int | `443` | HTTPS port |
| `username` | string | `admin` | PiKVM username |
| `password` | string | `admin` | PiKVM password |
| `otp_secret` | string | `null` | TOTP secret for 2FA |
| `verify_ssl` | bool | `false` | Verify TLS certificate |
| `cert_fingerprint` | string | `null` | SHA-256 fingerprint for cert pinning |

## Tailscale Setup

The recommended deployment has PiKVM on your Tailnet:

1. Install Tailscale on the PiKVM: https://docs.pikvm.org/tailscale/
2. The PiKVM gets a MagicDNS name like `pikvm-lab.ts.net`
3. Set `verify_ssl: false` (PiKVM uses self-signed certs)
4. For high-security deployments, grab the cert fingerprint and set `cert_fingerprint`

Self-signed SSL is the norm for PiKVM. The `verify_ssl: false` default is
intentional, not lazy.

When `cert_fingerprint` is set, Agentic-KVM first opens an unauthenticated TLS
connection to verify the PiKVM certificate fingerprint. Only after that
preflight succeeds does it create the authenticated HTTP client, using an SSL
context that trusts the pinned certificate as its only trust root. This keeps
PiKVM credentials off the wire when the presented certificate does not match.

## Audit Log

Every tool invocation is recorded in `/var/log/pikvm-mcp/session-<id>.jsonl`:

```json
{
  "ts": "2025-04-15T12:34:56.789Z",
  "session_id": "a1b2c3d4",
  "target_id": "lab-server",
  "operator_id": "operator@redteam",
  "tool": "pikvm_atx_power_on",
  "args": {"target": "lab-server"},
  "result": "ok",
  "duration_ms": 342
}
```

Passwords, tokens, OTP secrets, and internal client objects are automatically
stripped from logged arguments.

Typed HID text is redacted by default because it may contain passwords, recovery
keys, BIOS fields, or commands with secrets:

```json
{"tool": "type_text", "args": {"target": "lab-server", "text": "***"}}
```

For explicit engagement recording, set `PIKVM_FULL_CAPTURE=true`. Full capture
logs typed HID text, but still redacts credential-like fields such as passwords,
tokens, and OTP secrets. Only enable it when the operator and engagement scope
permit sensitive input capture.

## Development

```bash
uv sync
uv run pytest
uv run ruff check src/ tests/
```

### Live PiKVM Integration Tests

Integration tests are skipped by default. They load PiKVM settings from `.env`
and then overlay any `PIKVM_*` process environment variables.

Read-only integration smoke:

```bash
PIKVM_INTEGRATION=1 uv run pytest tests/integration -m integration
```

HID action smoke, such as moving the mouse, requires a second explicit opt-in:

```bash
PIKVM_INTEGRATION=1 PIKVM_ALLOW_HID_ACTIONS=1 \
  uv run pytest tests/integration -m "integration and hid_action"
```

Future MSD and ATX action tests should use the same pattern:
`PIKVM_ALLOW_MSD_ACTIONS=1` and `PIKVM_ALLOW_ATX_ACTIONS=1`. The MSD action
test expects an existing image in PiKVM storage and defaults to
`PIKVM_TEST_MSD_IMAGE=netboot.xyz.iso`.

```bash
PIKVM_INTEGRATION=1 PIKVM_ALLOW_MSD_ACTIONS=1 \
  uv run pytest tests/integration -m "integration and msd_action"
```

Keep destructive or state-changing tests opt-in even on lab hardware.

ATX action tests require both the general ATX opt-in and a specific action:

```bash
PIKVM_INTEGRATION=1 PIKVM_ALLOW_ATX_ACTIONS=1 \
  PIKVM_TEST_ATX_ACTION=power_on \
  uv run pytest tests/integration -m "integration and atx_action"
```

Use ATX actions only when the PiKVM ATX header wiring and LED state are known
to be reliable for the target.

## Available Tools

### MSD (Mass Storage Device)
- `pikvm_msd_state` — Current MSD state
- `pikvm_msd_upload_url` — Download ISO from URL into MSD storage
- `pikvm_msd_set_image` — Select image and mode (CD-ROM / flash)
- `pikvm_msd_connect` — Plug virtual drive into target
- `pikvm_msd_disconnect` — Unplug virtual drive

### ATX (Power Control)
- `pikvm_atx_state` — Power LED, HDD LED status
- `pikvm_atx_power_on` — Short press power button
- `pikvm_atx_power_off` — Long press power button
- `pikvm_atx_power_off_hard` — 5-second hold
- `pikvm_atx_reset` — Pulse reset button

### HID (Keyboard/Mouse)
- `pikvm_hid_state` — HID subsystem status
- `pikvm_screenshot` — Capture JPEG screenshot with detected resolution
- `pikvm_hid_type` — Type text string
- `pikvm_hid_send_key` — Press/release a key; one-shot presses auto-release by default
- `pikvm_hid_shortcut` — Press a multi-key shortcut
- `pikvm_mouse_move` — Move cursor using pixel or raw PiKVM absolute coordinates
- `pikvm_mouse_click` — Click at the current or specified cursor position
- `pikvm_mouse_scroll` — Scroll wheel events
- `pikvm_hid_calibrate` — Refresh screenshot-based mouse calibration

## Dependency Hardening Plan

The dogfood MVP currently uses broad dependency ranges and the checked-in
`uv.lock` for reproducible local installs. Before production hardening, the
project should move to pinned runtime dependencies, pinned Docker base/tool
images, and a 30-day dependency soak rule: newly released dependency versions
should not enter production images until they have aged for at least 30 days
unless a security fix requires an explicit exception.

The production hardening target for certificate pinning is per-connection peer
certificate enforcement in the HTTP transport, so every new TLS connection is
checked directly before authenticated request data is sent.

## License

MIT
