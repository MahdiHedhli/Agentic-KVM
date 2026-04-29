# Supermicro IPMI / BMC Component

Status: initial component design and implementation notes.

## Fit With Agentic-KVM

Supermicro IPMI support is aligned with the Agentic-KVM goal if it stays in
the out-of-band management lane.

It should not be treated as a replacement for PiKVM:

- IPMI can expose BMC power state, sensors, health, inventory, firmware, and
  system event logs.
- IPMI can request power actions such as on, off, shutdown, and reset.
- IPMI does not provide the PiKVM video/HID/MSD path that lets an agent see a
  screen, type into firmware menus, mount installer media, or drive a Mac/PC
  with no installed software.

The right product framing is: PiKVM gives the agent hands and eyes; IPMI gives
it a structured BMC health and power channel.

## Implementation Shape

The first implementation uses `pyghmi`, the OpenStack-maintained Python IPMI
library, instead of shelling out to `ipmitool`.

Reasons:

- avoids shell command construction and injection risk
- keeps BMC credentials in process memory rather than command arguments
- gives structured Python objects for sensors, inventory, event logs, and
  power state
- works inside the existing FastMCP Python process

The MCP tools are generic `ipmi_*` tools. Supermicro is the first tested lab
target, but IPMI itself is not Supermicro-specific.

## Configuration

Add IPMI targets with `IPMI_TARGETS`.

```bash
export IPMI_TARGETS='[
  {
    "name": "sm-lab",
    "host": "supermicro-ipmi.ts.net",
    "username": "ADMIN",
    "password": "change-me"
  }
]'
export IPMI_DEFAULT_TARGET=sm-lab
```

Optional fields:

| Field | Default | Notes |
|-------|---------|-------|
| `port` | `623` | RMCP/RMCP+ UDP port |
| `kg` | `null` | Optional IPMI Kg key |
| `privlevel` | `null` | Optional pyghmi privilege level override |
| `vendor` | `supermicro` | Label only; tools remain generic |

Use `PIKVM_ENV_FILE=.env` for local dogfood so MCP client config does not
carry BMC credentials.

## Tools

Read-only tools:

- `ipmi_power_state`
- `ipmi_health`
- `ipmi_sensors`
- `ipmi_event_log`
- `ipmi_inventory`
- `ipmi_firmware`
- `ipmi_system_power_watts`

Power tools:

- `ipmi_power_on`
- `ipmi_power_shutdown`
- `ipmi_power_off`
- `ipmi_power_reset`

Power tools are intentionally explicit. They should later be mapped to the
same security tiers and approval gates as PiKVM ATX actions.

## Security Notes

Recommended deployment:

- keep BMC/IPMI on a dedicated management network or Tailnet
- do not expose IPMI to the public internet
- create a dedicated BMC user for Agentic-KVM
- prefer the least privilege that still supports the required test
- use strong unique BMC credentials
- record BMC firmware version before testing
- start with read-only tools before enabling power actions

Operational caveats:

- Older IPMI implementations have a poor security history.
- Sensor and event-log data can include serials, hardware identifiers, and
  site-specific operational details.
- Power actions can interrupt storage writes and corrupt systems.
- A BMC reset is intentionally not exposed in the first tool set.

## First Live Test Plan

Set `IPMI_TARGETS` in `.env`, then restart the MCP server.

Read-only integration tests:

```bash
IPMI_INTEGRATION=1 uv run pytest tests/integration -m ipmi_integration
```

Start with:

```text
ipmi_power_state(target="sm-lab")
ipmi_health(target="sm-lab")
ipmi_sensors(target="sm-lab", only_unhealthy=true)
ipmi_event_log(target="sm-lab", limit=20)
```

Only after read-only behavior is confirmed:

```text
ipmi_power_state(target="sm-lab")
ipmi_power_shutdown(target="sm-lab", wait=false)
```

Use `ipmi_power_off` and `ipmi_power_reset` only when the server is explicitly
in lab scope and no useful state is at risk.

## References

- Supermicro IPMI utilities: https://www.supermicro.com/en/solutions/management-software/ipmi-utilities
- Supermicro BMC/IPMI user guide: https://www.supermicro.com/manuals/other/IPMI_Users_Guide.pdf
- pyghmi API reference: https://docs.openstack.org/pyghmi/latest/reference/index.html
