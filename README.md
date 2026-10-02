# InvisRing

InvisRing is a Windows proof of concept for sending and receiving short messages through the LED state of a ScpVBus virtual Xbox 360 controller. The sender changes the virtual controller's LED state through the xusb22 device interface; a separate receiver process polls that state and decodes the symbols.

This demonstrates a local shared-state communication channel that uses the controller stack. It does not demonstrate remote access, privilege escalation, kernel memory access, or that endpoint monitoring cannot observe the activity.

## How the channel works

The demo uses three LED values:

| Value | Meaning |
| --- | --- |
| `0x02` | Binary `0` |
| `0x03` | Binary `1` |
| `0x06` | Idle / symbol separator |

Frames include a synchronization preamble, payload length, UTF-8 payload, and CRC. The receiver records observed LED transitions and decode results as JSON captures under `Src/Capture/`.

The tool uses ScpVBus to provide the virtual controller and xusb22 to expose its controller interface and LED state. The demo code for discovery and LED requests is in `Src/Arch/`; the framing, sender, receiver, and stress harness are in `Src/Arch/led_link.py`.

## Requirements

- Windows with Python 3.10 or newer.
- ScpVBus installed and running.
- A ScpVBus virtual controller connected through the Windows Xbox controller stack (`xusb22`).
- Read/write access to the ScpVBus and xusb22 device interfaces.

## Run the demo

From the repository root:

```powershell
python .\Src\InvisRing.py
```

The menu contains:

- **Setup:** plug in virtual controller slots 1–4 and report whether each slot exists.
- **Test:** create a free slot, send left- and right-stick reports, verify them through XInput, then center and remove the test slot.
- **Exploit:** demonstrate InvisRing through the virtual LED channel, mass eject all slots, or issue the slot-zero mass-removal request.

The **Demonstrate InvisRing** entry sends a short, human-readable message through the LED state channel and verifies that a separate receiver process decodes it. It creates an `InvisRing_#NNNN` identifier, saves the full transition record as JSON under `Src/Capture/Raw/`, and saves the decoded message as a `.txt` file under `Src/Capture/`. The message uses the computer's hostname and locally queried HVCI status, plus the public IP and approximate city/region returned by one HTTPS request to `ipwho.is`. That service necessarily sees the network's source IP; with a VPN, the displayed IP/location will generally correspond to the VPN endpoint. The lookup values are only inserted into the demo message and its local captures.

The HVCI closing text uses mutually exclusive lines: when HVCI is on, it says `Despite all that, at least you have HVCI on!`; when off, it says `Do I even have to say it (HVCI: Off)`. If Windows cannot determine the state, it says so. The state comes from `Win32_DeviceGuard.SecurityServicesRunning` (value `2` means HVCI is running), rather than a hard-coded claim. [Microsoft documentation](https://learn.microsoft.com/en-us/windows/security/hardware-security/enable-virtualization-based-protection-of-code-integrity)

To send the default `Hello LED!` message with separate sender and receiver processes:

```powershell
python .\Src\Arch\led_link.py demo
```

To send a different message:

```powershell
python .\Src\Arch\led_link.py demo --message "Hello LED!"
```

The receiver writes `Src/Capture/led_capture.json` by default.

## Stress mode

Stress mode sends deterministic dummy text, checks the decoded payload, verifies its CRC, and records throughput and transition details:

```powershell
python .\Src\Arch\led_link.py stress
```

Defaults are a 2,048-byte payload with 20 ms pulse and gap intervals. It writes `Src/Capture/led_stress_capture.json`. Adjust payload size and timings with `--size`, `--pulse-ms`, and `--gap-ms`.

## Menu exploit entries

Stress mode remains available as a command-line research tool, but is not in the menu. The removal and eject demonstrations can disconnect virtual controllers. Run them only when that effect is intended.
