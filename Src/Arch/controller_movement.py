"""Create one ScpVBus slot and verify left/right stick reports through XInput."""

import ctypes
import struct
import time
from ctypes import wintypes

from Arch.bht_win32 import (
    CloseHandle,
    IOCTL_SCPVBUS_CONTROL,
    IOCTL_SCPVBUS_EXISTS,
    IOCTL_SCPVBUS_PLUGIN,
    IOCTL_SCPVBUS_REMOVE,
    discover_device,
    open_device,
    send_ioctl,
)


class _Gamepad(ctypes.Structure):
    _fields_ = [
        ("buttons", wintypes.WORD),
        ("left_trigger", ctypes.c_ubyte),
        ("right_trigger", ctypes.c_ubyte),
        ("lx", ctypes.c_short),
        ("ly", ctypes.c_short),
        ("rx", ctypes.c_short),
        ("ry", ctypes.c_short),
    ]


class _State(ctypes.Structure):
    _fields_ = [("packet", wintypes.DWORD), ("pad", _Gamepad)]


_xinput = ctypes.WinDLL("xinput1_4")
_get_state = _xinput.XInputGetState
_get_state.argtypes = [wintypes.DWORD, ctypes.POINTER(_State)]
_get_state.restype = wintypes.DWORD


def _states():
    result = {}
    for index in range(4):
        state = _State()
        if _get_state(index, ctypes.byref(state)) == 0:
            result[index] = (state.pad.lx, state.pad.ly, state.pad.rx, state.pad.ry)
    return result


def _report(lx=0, rx=0):
    return struct.pack("<BBHBBhhhh6x", 0, 20, 0, 0, 0, lx, 0, rx, 0)


def _observe(handle, slot, axis, expected):
    request = struct.pack("<II", 28, slot) + _report(**{axis: expected})
    sent = send_ioctl(handle, IOCTL_SCPVBUS_CONTROL, request, 9)
    if not sent["ok"]:
        print(f"  {axis} report failed: {sent['error']}")
        return False

    samples = []
    for _ in range(12):
        samples.append(_states())
        time.sleep(0.05)
    index = 0 if axis == "lx" else 2
    observed = any(values[index] == expected
                   for sample in samples for values in sample.values())
    print(f"  {axis}: expected {expected}; "
          f"{'observed' if observed else 'not observed'} via XInput")
    return observed


def run():
    paths = discover_device()
    if not paths:
        print("No ScpVBus interface found")
        return 1
    handle, error = open_device(paths[0])
    if not handle:
        print("ScpVBus open failed:", error)
        return 1

    slot = None
    try:
        print("[*] ScpVBus stick movement and XInput readback")
        for candidate in range(1, 5):
            exists = send_ioctl(handle, IOCTL_SCPVBUS_EXISTS,
                                struct.pack("<IIII", 16, candidate, 0, 0), 16)
            if exists["ok"]:
                continue
            plugged = send_ioctl(handle, IOCTL_SCPVBUS_PLUGIN,
                                 struct.pack("<IIII", 16, candidate, 0, 0), 16)
            if plugged["ok"]:
                slot = candidate
                break
        if slot is None:
            print("No free slot; existing controllers were left unchanged")
            return 2

        print(f"  Created test slot {slot}; waiting for XInput enumeration")
        time.sleep(1.0)
        left_ok = _observe(handle, slot, "lx", 16000)
        right_ok = _observe(handle, slot, "rx", 12000)
        return 0 if left_ok and right_ok else 3
    finally:
        if slot is not None:
            neutral = struct.pack("<II", 28, slot) + _report()
            send_ioctl(handle, IOCTL_SCPVBUS_CONTROL, neutral, 9)
            send_ioctl(handle, IOCTL_SCPVBUS_REMOVE,
                       struct.pack("<IIII", 16, slot, 0, 0), 16)
            print("  Centered and removed the test slot")
        CloseHandle(handle)
