"""Win32 device discovery and IOCTL helpers for the InvisRing demo."""

import ctypes
import os
import struct
import sys
from ctypes import wintypes

# ── Win32 setup ──────────────────────────────────────────────────────────────

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
ole32    = ctypes.WinDLL("ole32",    use_last_error=True)

# ── IOCTLs ───────────────────────────────────────────────────────────────────

GUID_SCPVBUS               = "{F679F562-3164-42CE-A4DB-E7DDBE723909}"
IOCTL_SCPVBUS_PLUGIN       = 0x2AA004
IOCTL_SCPVBUS_REMOVE       = 0x2AA008
IOCTL_SCPVBUS_EJECT        = 0x2AA00C
IOCTL_SCPVBUS_CONTROL      = 0x2AE010
IOCTL_SCPVBUS_EXISTS       = 0x2AE404


GUID_XUSB22_INTERFACE_0 = "{ec87f1e3-c13b-4100-b5f7-8b84d54260cb}"
GUID_XUSB22_INTERFACE_1 = "{a5dcbf10-6530-11d2-901f-00c04fb951ed}"

# IRP_MJ_DEVICE_CONTROL IOCTLs dispatched from IOCTL switch at 0x14001434c
IOCTL_XUSB_SLOT_COMMAND = 0x8000A010
IOCTL_XUSB_SLOT_STATUS = 0x8000E008

GENERIC_READ          = 0x80000000
GENERIC_WRITE         = 0x40000000
FILE_SHARE_READ       = 0x00000001
FILE_SHARE_WRITE      = 0x00000002
OPEN_EXISTING         = 3
ERROR_NO_MORE_ITEMS   = 259
DIGCF_PRESENT         = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010
INVALID_HANDLE_VALUE  = ctypes.c_void_p(-1).value

# ── GUID / SetupAPI structs ───────────────────────────────────────────────────

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize",              wintypes.DWORD),
        ("InterfaceClassGuid",  GUID),
        ("Flags",               wintypes.DWORD),
        ("Reserved",            ctypes.c_void_p),
    ]

CLSIDFromString = ole32.CLSIDFromString
CLSIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(GUID)]
CLSIDFromString.restype  = ctypes.c_long

SetupDiGetClassDevsW = setupapi.SetupDiGetClassDevsW
SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR,
                                  wintypes.HWND, wintypes.DWORD]
SetupDiGetClassDevsW.restype  = ctypes.c_void_p

SetupDiEnumDeviceInterfaces = setupapi.SetupDiEnumDeviceInterfaces
SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.POINTER(GUID), wintypes.DWORD,
                                         ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL

SetupDiGetDeviceInterfaceDetailW = setupapi.SetupDiGetDeviceInterfaceDetailW
SetupDiGetDeviceInterfaceDetailW.argtypes = [ctypes.c_void_p,
                                              ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
                                              ctypes.c_void_p, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.DWORD),
                                              ctypes.c_void_p]
SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL

SetupDiDestroyDeviceInfoList = setupapi.SetupDiDestroyDeviceInfoList
SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
SetupDiDestroyDeviceInfoList.restype  = wintypes.BOOL

CM_Get_Device_Interface_List_SizeW = ctypes.windll.cfgmgr32.CM_Get_Device_Interface_List_SizeW
CM_Get_Device_Interface_List_SizeW.argtypes = [ctypes.POINTER(wintypes.ULONG),
                                                ctypes.POINTER(GUID),
                                                wintypes.LPWSTR,
                                                wintypes.ULONG]
CM_Get_Device_Interface_List_SizeW.restype  = wintypes.LONG  # CONFIGRET

CM_Get_Device_Interface_ListW = ctypes.windll.cfgmgr32.CM_Get_Device_Interface_ListW
CM_Get_Device_Interface_ListW.argtypes = [ctypes.POINTER(GUID),
                                           wintypes.LPWSTR,
                                           wintypes.LPWSTR,
                                           wintypes.ULONG,
                                           wintypes.ULONG]
CM_Get_Device_Interface_ListW.restype  = wintypes.LONG  # CONFIGRET

# CM_Get_Device_Interface_List flags — NOT the same as CM_Get_Device_ID_List flags.
# CM_GET_DEVICE_INTERFACE_LIST_PRESENT = 0  (present/enabled interfaces only)
CM_GET_DEVICE_INTERFACE_LIST_PRESENT = 0

CreateFileW = kernel32.CreateFileW
CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                        wintypes.HANDLE]
CreateFileW.restype = wintypes.HANDLE

DeviceIoControl = kernel32.DeviceIoControl
DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID,
                            wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                            ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
DeviceIoControl.restype = wintypes.BOOL

CloseHandle = kernel32.CloseHandle

def winerr():
    err = ctypes.get_last_error()
    return err, ctypes.FormatError(err).strip()

def guid_from_string(s):
    g = GUID()
    hr = CLSIDFromString(s, ctypes.byref(g))
    if hr != 0:
        raise RuntimeError(f"CLSIDFromString failed: 0x{hr & 0xFFFFFFFF:08X}")
    return g

def discover_device():
    guid = guid_from_string(GUID_SCPVBUS)
    info = SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                 DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if info == INVALID_HANDLE_VALUE:
        return []
    paths = []
    try:
        idx = 0
        while True:
            iface = SP_DEVICE_INTERFACE_DATA()
            iface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            ctypes.set_last_error(0)
            ok = SetupDiEnumDeviceInterfaces(info, None, ctypes.byref(guid),
                                              idx, ctypes.byref(iface))
            if not ok:
                if ctypes.get_last_error() == ERROR_NO_MORE_ITEMS:
                    break
                break
            req = wintypes.DWORD()
            SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(iface),
                                              None, 0, ctypes.byref(req), None)
            buf = ctypes.create_string_buffer(req.value)
            cb  = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = cb
            ok = SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(iface),
                                                   buf, req.value,
                                                   ctypes.byref(req), None)
            if ok:
                paths.append(ctypes.wstring_at(ctypes.addressof(buf) + 4))
            idx += 1
    finally:
        SetupDiDestroyDeviceInfoList(info)
    return paths

def _cm_list_interfaces(iface_guid_str, instance_id=None):
    """
    Call CM_Get_Device_Interface_List[_Size]W and return all paths in the
    multi-string result as a Python list.

    iface_guid_str : an enabled xusb22 device-interface GUID
    instance_id    : real instance ID (backslash form) to filter by, or None = all

    The flag MUST be CM_GET_DEVICE_INTERFACE_LIST_PRESENT (0x0), NOT 0x100.
    0x100 is the flag for CM_Get_Device_ID_List — a different API entirely.
    Passing 0x100 here causes CM to return CR_INVALID_FLAG and an empty list.

    Returns list of path strings (may be empty).
    """
    try:
        guid = guid_from_string(iface_guid_str)
    except RuntimeError:
        return []

    # When instance_id is None we enumerate ALL interfaces for this GUID.
    # Must pass a true NULL pointer, not a zero-length unicode buffer.
    if instance_id:
        iid_w = ctypes.create_unicode_buffer(instance_id)
        iid_ptr = iid_w
    else:
        iid_ptr = None   # NULL → enumerate all

    flags = CM_GET_DEVICE_INTERFACE_LIST_PRESENT  # 0x0 — present interfaces only

    size = wintypes.ULONG(0)
    ret = CM_Get_Device_Interface_List_SizeW(
        ctypes.byref(size), ctypes.byref(guid), iid_ptr, flags)
    if ret != 0 or size.value <= 1:
        return []

    buf = ctypes.create_unicode_buffer(size.value)
    ret = CM_Get_Device_Interface_ListW(
        ctypes.byref(guid), iid_ptr,
        buf, size.value, flags)
    if ret != 0:
        return []

    # Multi-string: NUL-separated, double-NUL terminated
    # buf is a c_wchar_Array, not a byte array; decode the MULTI_SZ as wide chars.
    raw = ctypes.wstring_at(buf, size.value)
    return [path for path in raw.split("\x00") if path]






def open_device(path, access=GENERIC_READ | GENERIC_WRITE, flags=0):
    ctypes.set_last_error(0)
    h = CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE,
                    None, OPEN_EXISTING, flags, None)
    if h == INVALID_HANDLE_VALUE:
        return None, winerr()
    return h, None

def send_ioctl(handle, ioctl, payload=b"", out_size=4096):
    inbuf = ctypes.create_string_buffer(payload) if payload else None
    outbuf  = ctypes.create_string_buffer(out_size)
    ret_len = wintypes.DWORD()
    ctypes.set_last_error(0)
    ok = DeviceIoControl(handle, ioctl, inbuf, len(payload),
                         outbuf, out_size, ctypes.byref(ret_len), None)
    if not ok:
        return {"ok": False, "error": winerr(),
                "bytes_returned": ret_len.value, "data": b""}
    return {"ok": True, "error": None,
            "bytes_returned": ret_len.value,
            "data": outbuf.raw[:ret_len.value]}


# ── Terminal / menu helpers ───────────────────────────────────────────────────

if os.name == "nt":
    import msvcrt

    def _getch():
        ch = msvcrt.getch()
        if ch in (b"\x00", b"\xe0"):   # extended key prefix
            ch2 = msvcrt.getch()
            return ch + ch2
        return ch

    UP    = b"\xe0H"
    DOWN  = b"\xe0P"
    ENTER = b"\r"
    QUIT  = b"q"
else:
    import tty, termios

    def _getch():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.buffer.read(1)
            if ch == b"\x1b":
                ch += sys.stdin.buffer.read(2)
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    UP    = b"\x1b[A"
    DOWN  = b"\x1b[B"
    ENTER = b"\n"
    QUIT  = b"q"

CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
DIM    = "\033[2m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def cls():
    os.system("cls" if os.name == "nt" else "clear")

def print_result(r, label=""):
    if label:
        print(f"  {DIM}{label}{RESET}")
    if r["ok"]:
        print(f"  {GREEN}OK{RESET}  bytes_returned={r['bytes_returned']}", end="")
        if r["data"]:
            print(f"  data={r['data'].hex()}")
        else:
            print()
    else:
        code, msg = r["error"]
        print(f"  {RED}FAIL{RESET} ({code}: {msg})")

def separator():
    print(f"  {DIM}{'─'*58}{RESET}")

# Include the private key reader and interface helpers used by the menu.
__all__ = [name for name in globals() if not name.startswith("__")]
