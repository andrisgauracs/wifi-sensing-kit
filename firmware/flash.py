"""Flash + set up every board that's plugged in and listed in config.yaml.

  python firmware/flash.py             # full flash + WiFi/node settings (first time, or after another program)
  python firmware/flash.py --app-only  # just the program; keeps the WiFi/node settings (after a rebuild)

Boards are matched by MAC, so it doesn't matter which port they're on.
"""
import os, re, subprocess, sys, time
import serial

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import kitconfig  # noqa: E402
from discover import ports, mac_of, native  # noqa: E402

PROJ = os.path.join(HERE, ".build", "RuView", "firmware", "esp32-csi-node")
B = os.path.join(PROJ, "build")


def esptool(dev, *args):
    r = subprocess.run([sys.executable, "-m", "esptool", "--chip", "esp32s3", "--port", dev, "--baud", "460800", *args],
                       capture_output=True, text=True)
    return r.returncode == 0 and "Hash of data verified" in r.stdout, r.stdout + r.stderr


def flash(dev, c, app_only):
    if app_only:
        return esptool(dev, "--after", "hard-reset", "write-flash", "0x20000", f"{B}/esp32-csi-node.bin")
    # full image: replaces whatever was on the board (bootloader + partition table included)
    return esptool(dev, "write-flash", "--flash-mode", "dio", "--flash-size", c["firmware"].get("flash_size", "8MB"),
                   "0x0", f"{B}/bootloader/bootloader.bin", "0x8000", f"{B}/partition_table/partition-table.bin",
                   "0xf000", f"{B}/ota_data_initial.bin", "0x20000", f"{B}/esp32-csi-node.bin")


def provision(dev, c, node_id):
    r = subprocess.run([sys.executable, os.path.join(PROJ, "provision.py"), "--port", dev,
                        "--ssid", c["wifi"]["ssid"], "--password-file", c["wifi"]["password_file"],
                        "--target-ip", c["host"]["ip"], "--target-port", str(c["host"]["udp_port"]), "--node-id", str(node_id)],
                       capture_output=True, text=True, cwd=PROJ)
    for f in ("nvs_provision.bin", "nvs_config.csv"):   # don't leave the WiFi password lying around
        try: os.remove(os.path.join(PROJ, f))
        except FileNotFoundError: pass
    return r.returncode == 0 and "Hash of data verified" in r.stdout


def boot_check(dev, secs=12):
    s = serial.Serial(dev, 115200, timeout=0.2); s.dtr = False; s.rts = True; time.sleep(0.1); s.rts = False
    buf = b""; t0 = time.time()
    while time.time() - t0 < secs and b"yield=" not in buf.split(b"wifi:connected")[-1]:
        buf += s.read(4096)
    t = buf.decode(errors="replace")
    node = re.search(r"Node ID: (\d+)", t); wifi = re.search(r"connected with (.+?), aid", t)
    return (int(node.group(1)) if node else None), (wifi.group(1) if wifi else None), ("yield=" in t)


if __name__ == "__main__":
    c = kitconfig.load(); app_only = "--app-only" in sys.argv
    if not os.path.exists(f"{B}/esp32-csi-node.bin"):
        sys.exit("no firmware built yet — run python firmware/build.py")
    if not app_only and not os.path.exists(c["wifi"]["password_file"]):
        sys.exit(f"WiFi password file {c['wifi']['password_file']} not found (plain text, only the password, chmod 600)")
    by_mac = {b["mac"]: b for b in c["boards"]}
    found = 0
    for p in ports():
        mac = mac_of(p.device); b = by_mac.get(mac)
        if not b:
            print(f"{p.device}: {mac} is not in config.yaml — skipped"); continue
        found += 1; name = f"node {b['id']} ({b['colour']})"
        ok, log = flash(p.device, c, app_only)
        if not ok:
            print(f"{p.device}: {name} FLASH FAILED\n{log[-600:]}"); continue
        if not app_only and not provision(p.device, c, b["id"]):
            print(f"{p.device}: {name} flashed, but setting WiFi/node failed"); continue
        if native(p):
            node, ssid, streaming = boot_check(p.device)
            status = f"running as node {node}, joined {ssid!r}" + (", streaming" if streaming else " (not streaming yet)") if ssid else \
                     f"running as node {node}, NOT connected to WiFi — check the SSID/password and that it's 2.4 GHz"
        else:
            status = "flashed (UART socket: not reading the log, it would reset the board into download mode)"
        print(f"{p.device}: {name} — {status}")
    if not found:
        print("no configured boards found on USB — run python firmware/discover.py")
    else:
        print(f"\nboards send to {c['host']['ip']}:{c['host']['udp_port']} — start the dashboards: python kit/server.py")
