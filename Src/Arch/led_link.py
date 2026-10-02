"""Two-process LED state channel demo for a ScpVBus-backed xusb22 controller.

Run ``python Src/Arch/led_link.py demonstrate`` for the local InvisRing
demonstration, or use ``demo`` for a short custom message. Captures are written
under ``Src/Capture``. This is a controller-state experiment, not a remote
exfiltration claim or a test of gamepad button/stick input.
"""

import argparse
import binascii
from collections import deque
import json
import multiprocessing
import secrets
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import InvisRing as InvisRing


PREAMBLE = "1101010010110010"
ZERO_LED = 0x02
ONE_LED = 0x03
IDLE_LED = 0x06
MAX_MESSAGE_BYTES = 64
CAPTURE_DIR = Path(__file__).resolve().parents[1] / "Capture"
DEFAULT_OUTPUT = CAPTURE_DIR / "led_capture.json"
BYTE_SYNC = b"~LED2~"
BYTE_OUTPUT = CAPTURE_DIR / "led_stress_capture.json"


def _open_controller(number):
    wanted = f"#{number:07d}#"
    for path, mode in InvisRing.discover_xusb22_device():
        if mode != "RW" or InvisRing.GUID_XUSB22_INTERFACE_0.casefold() not in path.casefold():
            continue
        if wanted not in path.casefold():
            continue
        handle, error = InvisRing.open_device(path)
        if handle:
            return path, handle
        raise OSError(f"Could not open {path}: {error}")
    raise RuntimeError(f"No RW xusb22 interface for controller {number}")


def _get_led(handle):
    request = bytes((0, 1, 0))  # little-endian subcommand 0x0100, slot 0
    result = InvisRing.send_ioctl(handle, InvisRing.IOCTL_XUSB_SLOT_STATUS, request, 3)
    if not result["ok"] or len(result["data"]) < 3:
        raise OSError(f"GetLedState failed: {result['error']}")
    return result["data"][2]


def _set_led(handle, value):
    result = InvisRing.send_ioctl(handle, InvisRing.IOCTL_XUSB_SLOT_COMMAND,
                            InvisRing._xusb22_set_state(led=value, flags=1), 0)
    if not result["ok"]:
        raise OSError(f"SetState LED={value:#04x} failed: {result['error']}")


def _bytes_to_bits(data):
    return "".join(f"{byte:08b}" for byte in data)


def _bits_to_bytes(bits):
    return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))


def _frame(message):
    payload = message.encode("utf-8")
    if not payload or len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError(f"Message must be 1..{MAX_MESSAGE_BYTES} UTF-8 bytes")
    checksum = binascii.crc_hqx(payload, 0xFFFF)
    return PREAMBLE + f"{len(payload):08b}" + _bytes_to_bits(payload) + f"{checksum:016b}"


def send(message, controller, pulse_ms, gap_ms):
    bits = _frame(message)
    path, handle = _open_controller(controller)
    original = _get_led(handle)
    writes = 0
    started = time.perf_counter()
    print(f"Sender: {path}", flush=True)
    print(f"Sender: {len(bits)} symbols, {pulse_ms:g} ms pulse, {gap_ms:g} ms gap", flush=True)
    try:
        _set_led(handle, IDLE_LED)
        writes += 1
        time.sleep(gap_ms / 1000)
        for bit in bits:
            _set_led(handle, ONE_LED if bit == "1" else ZERO_LED)
            writes += 1
            time.sleep(pulse_ms / 1000)
            _set_led(handle, IDLE_LED)
            writes += 1
            time.sleep(gap_ms / 1000)
    finally:
        try:
            _set_led(handle, original)
            print(f"Sender: restored LED={original:#04x}", flush=True)
        finally:
            InvisRing.CloseHandle(handle)
    elapsed = time.perf_counter() - started
    result = {"message": message, "frame_bits": len(bits), "writes": writes,
              "elapsed_seconds": round(elapsed, 3),
              "frame_bits_per_second": round(len(bits) / elapsed, 2)}
    print(f"Sender: {json.dumps(result)}", flush=True)
    return result


def receive(controller, output, timeout, poll_ms, ready=None, stop=None):
    path, handle = _open_controller(controller)
    started = time.perf_counter()
    observed_changes = []
    sync = deque(maxlen=len(PREAMBLE))
    frame_bits = []
    expected_bits = None
    locked = False
    armed = False
    samples = 0
    decoded = None
    error = None
    last_led = None
    print(f"Receiver: {path}", flush=True)
    try:
        initial = _get_led(handle)
        last_led = initial
        armed = initial == IDLE_LED
        observed_changes.append({"at_seconds": 0.0, "led": initial})
        if ready is not None:
            ready.set()
        deadline = started + timeout
        while time.perf_counter() < deadline and (stop is None or not stop.is_set()):
            value = _get_led(handle)
            samples += 1
            now = time.perf_counter()
            if value != last_led:
                observed_changes.append({"at_seconds": round(now - started, 4), "led": value})
                last_led = value
            if value == IDLE_LED:
                armed = True
            elif armed and value in (ZERO_LED, ONE_LED):
                bit = "1" if value == ONE_LED else "0"
                armed = False
                if not locked:
                    sync.append(bit)
                    if "".join(sync) == PREAMBLE:
                        locked = True
                        print("Receiver: preamble found", flush=True)
                else:
                    frame_bits.append(bit)
                    if len(frame_bits) == 8:
                        size = int("".join(frame_bits), 2)
                        if size == 0 or size > MAX_MESSAGE_BYTES:
                            raise ValueError(f"Invalid frame length {size}")
                        expected_bits = 8 + size * 8 + 16
                    if expected_bits is not None and len(frame_bits) == expected_bits:
                        data = "".join(frame_bits)
                        size = int(data[:8], 2)
                        payload = _bits_to_bytes(data[8:8 + size * 8])
                        sent_crc = int(data[-16:], 2)
                        actual_crc = binascii.crc_hqx(payload, 0xFFFF)
                        if sent_crc != actual_crc:
                            raise ValueError(f"CRC mismatch: sent={sent_crc:04x}, got={actual_crc:04x}")
                        decoded = payload.decode("utf-8")
                        print(f"Receiver: decoded {decoded!r}", flush=True)
                        break
            time.sleep(poll_ms / 1000)
        if decoded is None and error is None:
            error = "Stopped before complete frame" if stop is not None and stop.is_set() else "Timed out before complete frame"
    except Exception as exc:
        error = str(exc)
    finally:
        InvisRing.CloseHandle(handle)
        elapsed = time.perf_counter() - started
        report = {
            "kind": "virtual-controller LED state channel demo",
            "controller": controller,
            "interface": path,
            "decoded_text": decoded,
            "complete": decoded is not None,
            "error": error,
            "led_changes": observed_changes,
            "measurements": {
                "elapsed_seconds": round(elapsed, 3),
                "polls": samples,
                "observed_changes": max(0, len(observed_changes) - 1),
                "frame_bits_after_preamble": len(frame_bits),
                "payload_bits_per_second": round(len(decoded.encode("utf-8")) * 8 / elapsed, 2)
                if decoded is not None else 0,
            },
        }
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Receiver: wrote {output} ({'complete' if decoded is not None else error})", flush=True)
    return report


def demo(args):
    ready = multiprocessing.Event()
    stop = multiprocessing.Event()
    output = Path(args.output).resolve()
    started_wall = time.time()
    receiver = multiprocessing.Process(target=receive,
        args=(args.controller, output, args.timeout, args.poll_ms, ready, stop))
    receiver.start()
    for _ in range(100):
        if ready.wait(0.1):
            break
        if not receiver.is_alive():
            raise RuntimeError("Receiver exited before opening the controller")
    else:
        stop.set()
        receiver.join(2)
        raise TimeoutError("Receiver did not become ready within 10 seconds")
    try:
        sent = send(args.message, args.controller, args.pulse_ms, args.gap_ms)
    except Exception:
        stop.set()
        receiver.join(3)
        raise
    receiver.join(max(1, args.timeout + 2))
    if receiver.is_alive():
        stop.set()
        receiver.join(3)
    if not output.exists() or output.stat().st_mtime < started_wall:
        raise RuntimeError("Receiver produced no capture file")
    report = json.loads(output.read_text(encoding="utf-8"))
    matched = report["decoded_text"] == sent["message"]
    print(f"Demo: decoded message {'matches' if matched else 'does not match'} sender", flush=True)
    print(f"Demo: capture {output}", flush=True)
    return 0 if matched else 1


def _stress_text(size):
    if not 1 <= size <= 8192:
        raise ValueError("Stress text size must be 1..8192 bytes")
    lines = []
    number = 0
    while sum(map(len, lines)) < size:
        lines.append(f"Hello LED! line {number:04d}: The quick brown fox talks through a virtual LED.\n")
        number += 1
    return "".join(lines)[:size].encode("ascii")


def _byte_frame(payload):
    frame = BYTE_SYNC + f"{len(payload):04X}".encode("ascii") + payload
    frame += f"{zlib.crc32(payload):08X}".encode("ascii")
    if IDLE_LED in frame:
        raise ValueError("Frame contains the reserved LED separator value 0x06")
    return frame


def send_byte_frame(payload, controller, pulse_ms, gap_ms):
    frame = _byte_frame(payload)
    path, handle = _open_controller(controller)
    original = _get_led(handle)
    started = time.perf_counter()
    print(f"Byte sender: {path}", flush=True)
    print(f"Byte sender: {len(payload)} payload bytes, {len(frame)} wire bytes, "
          f"{pulse_ms:g}/{gap_ms:g} ms pulse/gap", flush=True)
    try:
        _set_led(handle, IDLE_LED)
        time.sleep(gap_ms / 1000)
        for index, value in enumerate(frame, 1):
            _set_led(handle, value)
            time.sleep(pulse_ms / 1000)
            _set_led(handle, IDLE_LED)
            time.sleep(gap_ms / 1000)
            if index % 256 == 0:
                print(f"Byte sender: {index}/{len(frame)} wire bytes", flush=True)
    finally:
        try:
            _set_led(handle, original)
            restored = _get_led(handle)
            print(f"Byte sender: restored LED={restored:#04x}", flush=True)
        finally:
            InvisRing.CloseHandle(handle)
    elapsed = time.perf_counter() - started
    return {"payload_bytes": len(payload), "wire_bytes": len(frame),
            "writes": 2 * len(frame) + 2, "elapsed_seconds": round(elapsed, 3),
            "payload_bytes_per_second": round(len(payload) / elapsed, 2),
            "restored_led": restored}


def receive_byte_frame(controller, output, timeout, poll_ms, ready=None, stop=None):
    path, handle = _open_controller(controller)
    started = time.perf_counter()
    changes = []
    sync = deque(maxlen=len(BYTE_SYNC))
    frame = bytearray()
    expected = None
    locked = False
    armed = False
    samples = 0
    payload = None
    error = None
    last_led = None
    print(f"Byte receiver: {path}", flush=True)
    try:
        initial = _get_led(handle)
        last_led = initial
        armed = initial == IDLE_LED
        changes.append({"at_seconds": 0.0, "led": initial})
        if ready is not None:
            ready.set()
        deadline = started + timeout
        while time.perf_counter() < deadline and (stop is None or not stop.is_set()):
            value = _get_led(handle)
            samples += 1
            now = time.perf_counter()
            changed = value != last_led
            if changed:
                changes.append({"at_seconds": round(now - started, 4), "led": value})
                last_led = value
            if value == IDLE_LED:
                armed = True
            elif armed or changed:
                # A short idle interval can fall between receiver polls. A
                # new non-idle value is still a distinct byte even if that
                # separator was not sampled.
                armed = False
                if not locked:
                    sync.append(value)
                    if bytes(sync) == BYTE_SYNC:
                        locked = True
                        print("Byte receiver: sync found", flush=True)
                else:
                    frame.append(value)
                    if len(frame) == 4:
                        size = int(frame[:4].decode("ascii"), 16)
                        if not 1 <= size <= 8192:
                            raise ValueError(f"Invalid byte-frame length {size}")
                        expected = 4 + size + 8
                    if expected is not None and len(frame) == expected:
                        size = int(frame[:4].decode("ascii"), 16)
                        payload = bytes(frame[4:4 + size])
                        sent_crc = int(frame[-8:].decode("ascii"), 16)
                        actual_crc = zlib.crc32(payload)
                        if sent_crc != actual_crc:
                            raise ValueError(f"CRC mismatch: sent={sent_crc:08x}, got={actual_crc:08x}")
                        print(f"Byte receiver: decoded {len(payload)} bytes, CRC matched", flush=True)
                        break
                    if len(frame) % 256 == 0:
                        print(f"Byte receiver: {len(frame)} frame bytes", flush=True)
            time.sleep(poll_ms / 1000)
        if payload is None and error is None:
            if expected is not None:
                error = f"Incomplete frame: received {len(frame)}/{expected} bytes after sync"
            else:
                error = "Stopped before complete frame" if stop is not None and stop.is_set() else "Timed out before complete frame"
    except Exception as exc:
        error = str(exc)
        payload = None
    finally:
        InvisRing.CloseHandle(handle)
        elapsed = time.perf_counter() - started
        report = {
            "kind": "virtual-controller LED byte-channel capture",
            "controller": controller,
            "interface": path,
            "decoded_text": payload.decode("utf-8") if payload is not None else None,
            "complete": payload is not None,
            "error": error,
            "led_changes": changes,
            "measurements": {
                "elapsed_seconds": round(elapsed, 3),
                "polls": samples,
                "observed_changes": max(0, len(changes) - 1),
                "frame_bytes_after_sync": len(frame),
                "payload_bytes_per_second": round(len(payload) / elapsed, 2)
                if payload is not None else 0,
            },
        }
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Byte receiver: wrote {output} ({'complete' if payload is not None else error})",
              flush=True)
    return report


def stress(args):
    payload = _stress_text(args.size)
    ready = multiprocessing.Event()
    stop = multiprocessing.Event()
    output = Path(args.output).resolve()
    started_wall = time.time()
    receiver = multiprocessing.Process(target=receive_byte_frame,
        args=(args.controller, output, args.timeout, args.poll_ms, ready, stop))
    receiver.start()
    for _ in range(100):
        if ready.wait(0.1):
            break
        if not receiver.is_alive():
            raise RuntimeError("Byte receiver exited before opening the controller")
    else:
        stop.set()
        receiver.join(2)
        raise TimeoutError("Byte receiver did not become ready within 10 seconds")
    try:
        sent = send_byte_frame(payload, args.controller, args.pulse_ms, args.gap_ms)
    except Exception:
        stop.set()
        receiver.join(3)
        raise
    # The sender has finished, so the receiver has no reason to consume its
    # full timeout. Give it a brief window to validate the trailing CRC.
    receiver.join(3)
    if receiver.is_alive():
        stop.set()
        receiver.join(3)
    if not output.exists() or output.stat().st_mtime < started_wall:
        raise RuntimeError("Byte receiver produced no capture file")
    report = json.loads(output.read_text(encoding="utf-8"))
    matched = report["decoded_text"] == payload.decode("ascii")
    report["sender"] = sent
    report["message_matches"] = matched
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Stress: {'PASS' if matched else 'FAIL'}; "
          f"{sent['payload_bytes_per_second']} payload bytes/s; capture {output}", flush=True)
    return 0 if matched else 1


def _hvci_status():
    command = (
        "$d=Get-CimInstance -Namespace 'root\\Microsoft\\Windows\\DeviceGuard' "
        "-ClassName Win32_DeviceGuard; "
        "if ($d.SecurityServicesRunning -contains 2) {'ON'} "
        "elseif ($d.SecurityServicesConfigured -contains 2) {'OFF'} "
        "else {'OFF'}"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=8, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().splitlines()[-1].upper()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "UNKNOWN"


def _public_ip_and_location():
    """Fetch this connection's public IP and approximate location in one request."""
    request = Request(
        "https://ipwho.is/",
        headers={"User-Agent": "InvisRing-local-demo/1.0", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=8) as response:
            data = json.loads(response.read(64 * 1024).decode("utf-8"))
        if not data.get("success") or not data.get("ip"):
            raise ValueError(data.get("message", "lookup returned no IP"))
        city = data.get("city") or "Unknown city"
        region = data.get("region") or data.get("country") or "Unknown region"
        return str(data["ip"]), f"{city}, {region}"
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return "unavailable", f"unavailable (IP lookup failed: {exc})"


def demonstrate(args):
    demo_id = f"InvisRing_#{secrets.randbelow(9000) + 1000}"
    hostname = socket.gethostname()
    hvci = _hvci_status()
    public_ip, location = _public_ip_and_location()
    if hvci == "ON":
        hvci_sentence = "Despite all that, at least you have HVCI on!"
    elif hvci == "OFF":
        hvci_sentence = "Do I even have to say it (HVCI: Off)"
    else:
        hvci_sentence = "HVCI status could not be determined (HVCI: Unknown)"
    message = (
        f"Hello {hostname}, while you were watching a short cat gif, I was silently "
        f"using an invisible OLED to store facts about you! You are from {location}, "
        f"IP: {public_ip}. {hvci_sentence}"
    )
    payload = message.encode("utf-8")
    capture_dir = CAPTURE_DIR
    raw_path = capture_dir / "Raw" / f"{demo_id}.json"
    text_path = capture_dir / f"{demo_id}.txt"
    ready = multiprocessing.Event()
    stop = multiprocessing.Event()
    receiver = multiprocessing.Process(
        target=receive_byte_frame,
        args=(1, raw_path, args.timeout, args.poll_ms, ready, stop),
    )
    receiver.start()
    for _ in range(100):
        if ready.wait(0.1):
            break
        if not receiver.is_alive():
            raise RuntimeError("LED receiver exited before opening the controller")
    else:
        stop.set()
        receiver.join(2)
        raise TimeoutError("LED receiver did not become ready within 10 seconds")

    try:
        sent = send_byte_frame(payload, 1, args.pulse_ms, args.gap_ms)
    except Exception:
        stop.set()
        receiver.join(3)
        raise
    receiver.join(3)
    if receiver.is_alive():
        stop.set()
        receiver.join(3)
    if not raw_path.exists():
        raise RuntimeError("LED receiver produced no raw JSON capture")

    report = json.loads(raw_path.read_text(encoding="utf-8"))
    report["demonstration_id"] = demo_id
    report["local_facts"] = {
        "hostname": hostname,
        "hvci_status": hvci,
        "location": location,
        "public_ip": public_ip,
        "ip_and_location_are_sample_values": False,
        "external_lookup_performed": True,
        "lookup_service": "https://ipwho.is/",
        "lookup_values_used_only_in_demo_message_and_capture": True,
    }
    report["sender"] = sent
    complete = report.get("decoded_text") == message
    report["message_matches"] = complete
    raw_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if complete:
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(report["decoded_text"] + "\n", encoding="utf-8")
        print(f"\n{demo_id}\n{report['decoded_text']}\n", flush=True)
        print(f"Raw JSON: {raw_path}", flush=True)
        print(f"Decoded text: {text_path}", flush=True)
        print(f"LED channel: PASS ({sent['elapsed_seconds']} seconds)", flush=True)
        return 0
    print(f"LED channel: FAIL ({report.get('error')})", flush=True)
    print(f"Partial raw capture: {raw_path}", flush=True)
    return 1


def main():
    parser = argparse.ArgumentParser(description="ScpVBus/xusb22 LED state channel demo")
    sub = parser.add_subparsers(dest="mode")
    for mode in ("demo", "send", "receive"):
        command = sub.add_parser(mode)
        command.add_argument("--controller", type=int, choices=range(1, 5), default=1)
        if mode != "receive":
            command.add_argument("--message", default="Hello LED!")
            command.add_argument("--pulse-ms", type=float, default=120)
            command.add_argument("--gap-ms", type=float, default=120)
        if mode != "send":
            command.add_argument("--output", default=str(DEFAULT_OUTPUT))
            command.add_argument("--timeout", type=float, default=60)
            command.add_argument("--poll-ms", type=float, default=10)
    stress_command = sub.add_parser("stress", help="Send deterministic text as LED byte symbols")
    stress_command.add_argument("--controller", type=int, choices=range(1, 5), default=1)
    stress_command.add_argument("--size", type=int, default=2048, help="Text bytes (default: 2048)")
    stress_command.add_argument("--pulse-ms", type=float, default=20)
    stress_command.add_argument("--gap-ms", type=float, default=20)
    stress_command.add_argument("--poll-ms", type=float, default=1)
    stress_command.add_argument("--timeout", type=float, default=180)
    stress_command.add_argument("--output", default=str(BYTE_OUTPUT))
    demo_command = sub.add_parser("demonstrate", help="send the short InvisRing local demo")
    demo_command.add_argument("--pulse-ms", type=float, default=30)
    demo_command.add_argument("--gap-ms", type=float, default=25)
    demo_command.add_argument("--poll-ms", type=float, default=1)
    demo_command.add_argument("--timeout", type=float, default=45)
    args = parser.parse_args()
    if args.mode is None:
        args = parser.parse_args(["demo"])
    if getattr(args, "pulse_ms", 1) <= 0 or getattr(args, "gap_ms", 1) <= 0:
        parser.error("Pulse and gap times must be positive")
    if getattr(args, "poll_ms", 1) <= 0 or getattr(args, "timeout", 1) <= 0:
        parser.error("Poll interval and timeout must be positive")
    if args.mode == "demo":
        return demo(args)
    if args.mode == "stress":
        return stress(args)
    if args.mode == "demonstrate":
        return demonstrate(args)
    if args.mode == "send":
        send(args.message, args.controller, args.pulse_ms, args.gap_ms)
        return 0
    report = receive(args.controller, args.output, args.timeout, args.poll_ms)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
