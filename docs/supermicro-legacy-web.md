# Supermicro Legacy Web iKVM / Virtual Media

This project now includes a Supermicro-specific legacy web component for older
X10/X11-era BMC firmware where Redfish iKVM / virtual media is present but
license-gated or incomplete.

## What Works

- `supermicro_vm_status`
- `supermicro_vm_config_get`
- `supermicro_vm_config_set`
- `supermicro_vm_mount`
- `supermicro_vm_unmount`
- `supermicro_ikvm_jnlp`

These tools use the legacy CGI interface behind the BMC web UI:

- login: `/cgi/login.cgi`
- VM status / mount / unmount / config: `/cgi/op.cgi`, `/cgi/ipmi.cgi`
- iKVM JNLP: `/cgi/url_redirect.cgi?url_name=ikvm&url_type=jwsk`

## Implementation Notes

- The legacy CGI layer is CSRF-protected.
- The authenticated page injects a `CSRF_TOKEN` value via `SmcCsrfInsert(...)`.
- CGI POSTs must carry that token as an HTTP header.
- The session is cookie-based (`SID`).

## Security Notes

- The legacy web interface is a privileged management plane and should stay on a
  management VLAN or Tailnet. Do not expose it directly to the public internet.
- The Java iKVM path is vendor-specific and dated. Treat any future wrapper
  around the ATEN Java viewer as higher-risk than the PiKVM control plane.
- Prefer TLS pinning via `web_cert_fingerprint` for deployments where the BMC
  certificate is stable enough to pin.

## Known Gaps

1. `supermicro_ikvm_jnlp` only extracts the launch descriptor. It does not yet
   provide a native framebuffer / keyboard / mouse implementation.
2. The legacy `vm_status` endpoint on firmware `03.88` accepted config and
   mount/unmount commands during live testing, but still reported
   `mounted_status=0`. Treat status as advisory until correlated with actual
   boot behavior.
3. Redfish iKVM and Redfish virtual-media endpoints may still be preferable on
   newer Supermicro platforms where they are fully licensed and HTML5-backed.
