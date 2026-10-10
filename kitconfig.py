"""Loads and checks config.yaml. Shared by the firmware tools and the server."""
import os, re, socket, sys
import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
COLOURS = {"red": (40, 0, 0), "green": (0, 40, 0), "blue": (0, 0, 40), "yellow": (30, 26, 0),
           "magenta": (32, 0, 32), "cyan": (0, 30, 30), "white": (24, 24, 24)}
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def load(path=None):
    path = path or os.path.join(ROOT, "config.yaml")
    if not os.path.exists(path):
        sys.exit(f"{path} not found — copy config.example.yaml to config.yaml and fill it in")
    c = yaml.safe_load(open(path))
    c["router_bssid"] = str(c.get("router_bssid", "")).lower()
    for b in c.get("boards", []):
        b["mac"] = str(b["mac"]).lower()
        b["colour"] = b.get("colour", "white")
        if not MAC_RE.match(b["mac"]):
            sys.exit(f"board {b.get('id')}: '{b['mac']}' is not a MAC address (aa:bb:cc:dd:ee:ff)")
        if b["colour"] not in COLOURS:
            sys.exit(f"board {b['id']}: colour must be one of {', '.join(COLOURS)}")
    ids = [b["id"] for b in c["boards"]]
    if len(set(ids)) != len(ids) or not all(isinstance(i, int) and 1 <= i <= 255 for i in ids):
        sys.exit("board ids must be unique integers 1-255")
    c["wifi"]["password_file"] = os.path.expanduser(c["wifi"]["password_file"])
    c["firmware"]["idf_path"] = os.path.expanduser(c["firmware"].get("idf_path", "~/esp/esp-idf"))
    if str(c["host"].get("ip", "auto")) == "auto":
        c["host"]["ip"] = local_ip()
    return c


def local_ip():
    """The address this computer uses on the LAN (no packets are actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1)); return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def senders(c):
    """MAC -> short name used for links: 'R' for the router, '1', '2', … for boards."""
    out = {b["mac"]: str(b["id"]) for b in c["boards"]}
    if MAC_RE.match(c["router_bssid"]):
        out[c["router_bssid"]] = "R"
    return out
