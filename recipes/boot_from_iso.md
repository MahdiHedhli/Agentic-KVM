# Recipe: Boot from Installer ISO

Boot a remote machine from an ISO image using PiKVM MSD and ATX controls.
This is the canonical end-to-end workflow that proves MSD + ATX + HID work together.

## Prerequisites

- PiKVM target configured and reachable on the Tailnet
- ISO URL accessible from the PiKVM device (not your local machine)
- Sufficient MSD storage for the ISO

## Steps

### 1. Check current state

```
pikvm_msd_state(target="lab-server")
pikvm_atx_state(target="lab-server")
```

Verify MSD is enabled, not busy, and has enough free space.
Check if the machine is currently powered on.

### 2. Upload the ISO

```
pikvm_msd_upload_url(
    url="https://releases.ubuntu.com/24.04/ubuntu-24.04-live-server-amd64.iso",
    target="lab-server"
)
```

This is a long-running operation. PiKVM downloads the ISO server-side.
For a 2GB ISO on a fast connection, expect 2-5 minutes.

### 3. Select and mount the image

```
pikvm_msd_set_image(image="ubuntu-24.04-live-server-amd64.iso", cdrom=True, target="lab-server")
pikvm_msd_connect(target="lab-server")
```

Setting `cdrom=True` presents the image as a virtual CD-ROM drive,
which is what most BIOS/UEFI boot menus expect for OS installers.

### 4. Power cycle into boot menu

If the machine is off:
```
pikvm_atx_power_on(target="lab-server")
```

If the machine is already on, reset it:
```
pikvm_atx_reset(target="lab-server")
```

### 5. Enter boot menu

Immediately after power-on/reset, send the boot menu key.
Common keys by vendor:

| Vendor    | Boot Menu Key |
|-----------|--------------|
| Dell      | F12          |
| HP        | F9           |
| Lenovo    | F12          |
| Supermicro| F11          |
| UEFI      | Escape       |

```
pikvm_hid_send_key(key="F12", target="lab-server")
```

You may need to send this repeatedly during POST.  A screenshot loop
can confirm when the boot menu appears.

### 6. Select the virtual CD-ROM

Use HID keyboard navigation (arrow keys + Enter) to select the
virtual CD-ROM entry from the boot menu.

### 7. Clean up after installation

Once the OS installer completes:
```
pikvm_msd_disconnect(target="lab-server")
pikvm_atx_reset(target="lab-server")
```

The machine will now boot from its internal drive with the freshly
installed OS.

## Audit Trail

Every step above is logged to the session JSONL file.  The audit
record provides a complete chain-of-custody for red team engagements:
who did what, to which target, and when.

## Failure Modes

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| MSD upload hangs | PiKVM can't reach the URL | Ensure the ISO URL is accessible from the PiKVM's network |
| Boot menu doesn't appear | Wrong key or timing | Try different vendor keys; send key repeatedly |
| "MSD busy" error | Previous operation still running | Wait and check `pikvm_msd_state` |
| Machine doesn't power on | ATX cable not connected | Physical access required to check ATX header connection |
