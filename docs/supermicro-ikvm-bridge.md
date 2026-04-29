# Supermicro iKVM Bridge

The current implementation does not attempt to reimplement the proprietary
ATEN/Supermicro KVM transport. Instead, it wraps the vendor Java Web Start
viewer in a local bridge.

## What It Does

- Fetches an authenticated JNLP descriptor from the BMC
- Writes a short-lived local launch bundle under `PIKVM_RUNTIME_DIR`
- Launches a local Docker container that runs:
  - `Xvfb`
  - `fluxbox`
  - `x11vnc`
  - `websockify` / noVNC
  - `javaws /session/ikvm.jnlp`
- Returns a localhost URL such as `http://127.0.0.1:6080/vnc.html`

## MCP Tools

- `supermicro_ikvm_prepare_bundle`
- `supermicro_ikvm_launch_bridge`
- `supermicro_ikvm_stop_bridge`

## Security Notes

- The generated JNLP bundle contains authenticated session tokens and should be
  treated as sensitive and short-lived.
- The bridge only publishes to loopback (`127.0.0.1`), not all interfaces.
- Keep the BMC itself on a management VLAN or Tailnet.

## Operational Notes

- This path depends on Docker being installed locally.
- The first bridge launch may build the local runtime image.
- The Java viewer still comes from the BMC vendor path, so failures in the Java
  client or BMC firmware should be expected on older systems.
