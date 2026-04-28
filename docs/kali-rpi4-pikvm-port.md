# Kali RPi4 PiKVM Port And Pentest Sidecar

Status: experimental design note. This is not the recommended production path
for the v0.1 dogfood build.

## Decision

Keep PiKVM OS as the default KVM control-plane image.

Use Kali as a separate MCP-controlled pentest environment first:

- as a local or remote Docker sidecar
- as a second device on the same Tailnet
- as a VM or cloud host when CPU/RAM/storage requirements exceed the KVM device

Porting PiKVM onto Kali Raspberry Pi 4 is possible as a lab experiment, but it
should not block the product path. The reliable appliance path is still:

1. PiKVM OS runs video capture, HID, MSD, ATX, and the PiKVM API.
2. Agentic-KVM exposes PiKVM control through MCP.
3. A separate Kali tool runtime exposes approved pentest commands through MCP.
4. Frontier model CLI wrappers call one or both MCP servers from the same
   operator workflow.

## Why Not Make Kali The PiKVM Host First?

PiKVM OS is an Arch Linux ARM-based appliance image with PiKVM-specific package
repositories, tuned services, and special partitioning. The official PiKVM FAQ
states that the project distributes OS images because PiKVM requires OS tuning
and memory-card partitioning. That means a Kali port is not just "install a
package"; it is an appliance rebase.

The risk is not only build complexity. Kali is designed to be a penetration
testing distribution. Putting offensive tools directly on the same OS that
owns KVM, HID, virtual media, power control, and credentials expands the blast
radius of a compromise or operator mistake.

For a red-team appliance, the control plane should be boring, minimal, and
recoverable. The tool plane can be sharp.

## Kali RPi4 PiKVM Port Scope

An experimental Kali RPi4 PiKVM port would need to recreate these PiKVM OS
capabilities on top of Kali:

- `kvmd` service and configuration
- `ustreamer` video capture pipeline
- nginx or equivalent API/frontend reverse proxy
- PiKVM authentication behavior
- USB gadget mode for keyboard, mouse, and mass storage
- MSD storage layout and image-management behavior
- GPIO/ATX integration for power controls
- HDMI capture support for the selected hardware
- keymaps and HID event handling
- read-only or controlled-write filesystem behavior
- update and rollback procedure
- Tailscale-first remote access

Minimum compatibility target for Agentic-KVM:

- `GET /api/streamer`
- `GET /api/streamer/snapshot`
- `GET /api/hid`
- `POST /api/hid/print`
- `POST /api/hid/events/send_key`
- `POST /api/hid/events/send_shortcut`
- `POST /api/hid/events/send_mouse_move`
- `POST /api/hid/events/send_mouse_button`
- `POST /api/hid/events/send_mouse_wheel`
- `GET /api/msd`
- `POST /api/msd/set_params`
- `POST /api/msd/connect`
- `POST /api/msd/disconnect`
- `POST /api/msd/write_remote`
- `GET /api/atx`
- `POST /api/atx/click`

Exit criteria for calling the port viable:

- Agentic-KVM offline tests still pass.
- Agentic-KVM live read-only integration tests pass.
- HID action tests pass without stuck keys.
- MSD URL upload, set-image, connect, and disconnect pass.
- Streamer offline/wake behavior is understood and documented.
- ATX state is reliable enough to make power actions safe or is explicitly
  disabled for that build.
- Re-imaging the device is faster than debugging it in place.

## Recommended Near-Term Architecture: Kali MCP Sidecar

The near-term product shape should be a two-plane appliance.

```text
                       Operator / Frontier Model App
                                  |
                                  | MCP
                                  v
                 +----------------+----------------+
                 |                                 |
                 v                                 v
        Agentic-KVM MCP Server             Kali Tools MCP Server
        PiKVM control plane                pentest tool plane
                 |                                 |
                 v                                 v
            PiKVM OS / API               Kali container / VM / host
                 |
                 v
          Target PC / Mac / Server
```

### Control Plane

The control plane owns:

- video observation
- keyboard and mouse events
- virtual media
- ATX power controls
- audit trail for KVM actions
- target allow-list and future approval policy

The control plane should not need a full Kali userland.

### Tool Plane

The tool plane owns:

- network assessment commands
- web testing tools
- wordlists and payload tooling
- report artifacts
- long-running scans when explicitly authorized
- disposable tool state

For the first implementation, use official Kali Docker images as the base.
Kali documents `kalilinux/kali-rolling` as the main official rolling image.
Those images are intentionally minimal and do not include the default toolset,
so the release image must explicitly install the required metapackages or
individual tools.

Recommended first image:

```dockerfile
FROM kalilinux/kali-rolling

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       ca-certificates \
       curl \
       dnsutils \
       iproute2 \
       iputils-ping \
       kali-linux-headless \
       nmap \
       python3 \
       python3-pip \
       tcpdump \
       traceroute \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
```

Use a narrow MCP wrapper for tools rather than exposing arbitrary shell by
default. A shell tool is useful for dogfood, but it is too broad for a client
or production red-team appliance.

Initial MCP tools should be explicit:

- `kali_nmap_scan`
- `kali_http_probe`
- `kali_dns_lookup`
- `kali_tcpdump_capture`
- `kali_file_read_artifact`
- `kali_list_artifacts`

Later, add a gated `kali_shell` only for lab or full-capture engagements.

## Frontier Model Wrappers And CLI Binaries

The release should include wrappers, not vendored vendor credentials or hidden
interactive setup.

Recommended layout:

```text
wrappers/
  claude/
    mcp.json
    launch.sh
  codex/
    mcp.json
    launch.sh
  gemini/
    mcp.json
    launch.sh
  generic/
    mcp.json
    launch.sh
```

Each wrapper should:

- point to the Agentic-KVM MCP server
- optionally point to the Kali Tools MCP server
- load secrets from local environment files or the platform credential store
- fail closed when required credentials are missing
- print the active target and enabled capability tier before launch
- avoid embedding API keys, PiKVM passwords, Tailnet hostnames, or cert
  fingerprints in committed files

Do not vendor closed-source frontier model binaries into the repo unless the
license and update channel explicitly permit redistribution. Instead, release:

- install checks
- launch scripts
- sample MCP client config
- version reporting
- documented expected binary names

For an appliance image, include an installer step that fetches vendor CLIs from
their official sources during provisioning, then records binary versions in the
appliance manifest.

## Security Policy For Sidecar Tools

The Kali sidecar must not bypass the security model.

Required defaults:

- no privileged container by default
- no host network by default
- no Docker socket mounted into the tool container
- no PiKVM credential material inside the Kali container
- no arbitrary shell exposed to agents by default
- artifacts stored under a dedicated mounted volume
- clear target allow-list
- explicit operator approval for scans outside the allow-list
- scan logs and artifacts separated from KVM audit logs but correlated by
  session ID

Allowed exceptions for lab mode:

- host networking for tools that need raw sockets
- `CAP_NET_RAW` and `CAP_NET_ADMIN` for packet capture or network tooling
- privileged mode only for explicitly documented hardware-adjacent workflows

Any exception should be visible in the MCP server startup log and in the audit
record.

## Release Shape

Near-term release artifacts:

- Agentic-KVM MCP server
- optional Kali Tools MCP server
- Docker Compose file for local dogfood
- PiKVM OS setup notes
- Kali sidecar setup notes
- frontier model wrapper templates
- sample `.env.example` files without secrets
- security model documentation
- appliance manifest format

Do not ship the Kali RPi4 PiKVM port as the default image until it has its own
repeatable build, rollback path, and live hardware test matrix.

## Open Questions

- Should the Kali sidecar run on the PiKVM device, a second Raspberry Pi, or a
  small x86 box by default?
- Should the first Kali Tools MCP server be part of this repo or a sibling repo?
- Which tool categories belong in v0.1: network, web, wireless, credential
  auditing, reporting, or only safe primitives?
- What is the minimum frontier-model wrapper set for the first release?
- What artifact format should tie KVM audit events to Kali tool output?

## References

- PiKVM FAQ: https://docs.pikvm.org/faq/
- PiKVM API: https://docs.pikvm.org/api/
- Kali Raspberry Pi 4 docs: https://www.kali.org/docs/arm/raspberry-pi-4/
- Official Kali Docker images: https://www.kali.org/docs/containers/official-kalilinux-docker-images/
- Installing Docker on Kali: https://www.kali.org/docs/containers/installing-docker-on-kali/
