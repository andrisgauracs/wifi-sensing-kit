"""Find your boards and your router's address, for config.yaml.

  python firmware/discover.py            # list ESP32 boards plugged in over USB, with their MACs
  python firmware/discover.py --router   # read a flashed board's boot log for the router it joined
"""
import re, subprocess, sys, time
import serial
from serial.tools import list_ports


def ports():
    return [p for p in list_ports.comports() if p.vid in (0x303A, 0x10C4, 0x1A86) or "usbmodem" in p.device or "ttyACM" in p.device]


def mac_of(dev):
    out = subprocess.run([sys.executable, "-m", "esptool", "--port", dev, "read-mac"], capture_output=True, text=True).stdout
    m = re.search(r"MAC:\s+([0-9a-f:]{17})", out)
    return m.group(1) if m else None


def native(p):
    """Espressif's built-in USB (VID 0x303A) is safe to open; a USB-UART bridge socket can reset the
    board into download mode whenever a program opens the port."""
    return p.vid == 0x303A


def router_from_log(dev, secs=12):
    s = serial.Serial(dev, 115200, timeout=0.2); s.dtr = False; s.rts = True; time.sleep(0.1); s.rts = False
    buf = b""; t0 = time.time()
    while time.time() - t0 < secs:
        buf += s.read(4096)
        m = re.search(rb"connected with (.+?), aid = \d+, channel (\d+).*?bssid = ([0-9a-f:]{17})", buf)
        if m:
            return m.group(1).decode(errors="replace"), int(m.group(2)), m.group(3).decode()
    return None


if __name__ == "__main__":
    ps = ports()
    if not ps:
        sys.exit("no ESP32 boards found on USB — use a data cable (charge-only cables don't show up)")
    if "--router" in sys.argv:
        for p in ps:
            r = router_from_log(p.device)
            if r:
                ssid, ch, bssid = r
                print(f'joined "{ssid}" on channel {ch}\n\nrouter_bssid: "{bssid}"'); break
        else:
            print("no board reported a WiFi connection — flash and provision one first (firmware/flash.py)")
        sys.exit()
    print("boards:\n")
    for i, p in enumerate(ps, 1):
        mac = mac_of(p.device)
        warn = "" if native(p) else "   # UART socket — prefer the board's native USB socket"
        print(f'  - {{ id: {i}, mac: "{mac}", colour: {["red", "green", "blue", "yellow", "magenta", "cyan"][(i - 1) % 6]} }}   # {p.device}{warn}')
