# Troubleshooting

**`discover.py` finds no boards.** Charge-only USB cables carry no data — try another cable.

**Board has two USB-C sockets (often "USB" and "UART").** Use the native one (Espressif's own USB;
on macOS it shows up as `/dev/cu.usbmodem<serial>`). Opening the UART socket from a program can reset
the board into download mode ("waiting for download" in its log), so it never starts the sensing
program. Either socket is fine on a plain USB charger.

**A board was used for something else (Arduino, another IDF project).** Run `firmware/flash.py`
without `--app-only`: it rewrites the bootloader and partition table too. If its log shows
`No bootable app partitions`, that's the symptom.

**Board flashed but not on the dashboard.**
- `flash.py` reports "NOT connected to WiFi": wrong SSID/password, or a 5 GHz-only network.
- Connected but nothing arrives: your computer's IP changed since flashing (DHCP). Re-run `flash.py`
  (it re-provisions), and give the computer a fixed IP / DHCP reservation in your router.
- Firewall: allow incoming UDP on `host.udp_port` (default 5005).
- `Association refused` once in the log is normal; it retries.

**The LED flickers instead of a steady colour.** You flashed stock RuView firmware (its default
40 Hz motion visualiser). Build with `firmware/build.py`, which turns it off.

**Only some links stream.** Boards far apart or behind walls may hear each other weakly, and the
router uses up a nearby board's ~50/s budget. Move boards so each has a clear view of the others.
The server simply skips links under 5 frames a second.

**Movement page: "saved: 0 windows" / red message.**
- "camera unsure": the camera can't see you well — head to feet in frame, decent light.
- Red banner "another movement page is open": two tabs send conflicting labels; close one.
- "fewer than 3 WiFi links": a board lost power or WiFi.

**"Camera sees" never says squatting / sitting / spinning.** Feet must be in frame (leave some floor
below them). Sit facing the camera or at an angle, not fully side-on. Spin slowly (one turn every
2–3 s) on one spot.

**"links changed … clear first".** You trained with a set of links that isn't streaming any more
(a board moved or is off). Bring it back, or **Clear all** and retrain.

**Room map lines all light up wherever I walk.** That's real: WiFi reflects off every wall, so you
disturb every link from almost anywhere. Use the calibration (rounds) to learn areas rather than
expecting a moving dot.
