"""xusb22 interface discovery and LED request helpers for InvisRing."""

from Arch.bht_win32 import *
from Arch.bht_win32 import _cm_list_interfaces


def discover_xusb22_device():
    """
    Discover Xbox 360 controller FDO paths through the observed interface GUIDs.

    Tries CM_Get_Device_Interface_List first (preferred), then SetupDi as fallback.
    The generic USB interface GUID also covers unrelated devices, so paths must
    match a controller parent instance, without an &IG_ interface suffix.
    Returns list of (path, access_mode) where access_mode is 'RW', 'RO', or None.
    """
    seen = {}

    def _test_and_record(path):
        upper = path.upper()
        if "USB#VID_045E&PID_028E#" not in upper or "&IG_" in upper:
            return
        key = path.casefold()
        if key in seen:
            return
        h, _ = open_device(path, GENERIC_READ | GENERIC_WRITE)
        if h:
            CloseHandle(h)
            seen[key] = (path, "RW")
            return
        h2, _ = open_device(path, GENERIC_READ)
        if h2:
            CloseHandle(h2)
            seen[key] = (path, "RO")
            return
        seen[key] = (path, None)

    for guid_str in (GUID_XUSB22_INTERFACE_0, GUID_XUSB22_INTERFACE_1):
        # CM path (works without DIGCF_DEVICEINTERFACE requirement)
        for path in _cm_list_interfaces(guid_str):
            _test_and_record(path)
        # SetupDi fallback
        try:
            guid = guid_from_string(guid_str)
            info = SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                         DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
            if info == INVALID_HANDLE_VALUE:
                continue
            try:
                idx = 0
                while True:
                    iface = SP_DEVICE_INTERFACE_DATA()
                    iface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
                    ctypes.set_last_error(0)
                    ok = SetupDiEnumDeviceInterfaces(info, None, ctypes.byref(guid),
                                                      idx, ctypes.byref(iface))
                    if not ok:
                        break
                    req = wintypes.DWORD()
                    SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(iface),
                                                      None, 0, ctypes.byref(req), None)
                    if req.value >= 6:
                        buf = ctypes.create_string_buffer(req.value)
                        cb  = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
                        ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = cb
                        ok2 = SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(iface),
                                                                buf, req.value,
                                                                ctypes.byref(req), None)
                        if ok2:
                            _test_and_record(ctypes.wstring_at(ctypes.addressof(buf) + 4))
                    idx += 1
            finally:
                SetupDiDestroyDeviceInfoList(info)
        except Exception:
            continue

    return list(seen.values())


def _xusb22_set_state(slot=0, led=0, left=0, right=0, flags=0):
    """Build DispatchSetState's nine-byte request for subcommand 0x0101."""
    request = bytearray(9)
    request[:5] = bytes((slot, led, left, right, flags))
    struct.pack_into("<H", request, 5, 0x0101)
    return bytes(request)


__all__ = ['discover_xusb22_device', '_xusb22_set_state']
