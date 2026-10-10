"""wifi-sensing-kit live server: receives CSI from the boards and serves the dashboards.

  python kit/server.py          ->  http://127.0.0.1:<http_port>  (signals · room map · movement)

The boards (RuView esp32-csi-node v0.8.8 + this kit's patch) send UDP to this computer:
  0xC5110001  CSI frame   [20-byte header][int8 I/Q pairs][6-byte transmitter MAC if byte 19 bit 7]
  0xC5110002  vitals      32 bytes: presence / motion / breathing (computed on the board)
Each sender -> receiver pair is a "link" ("R>1" = router to board 1, "2>3" = board 2 to board 3).
Everything you record (room calibration, movement training/test) is saved under data/.
"""
import json, os, socket, struct, sys, threading, time
from urllib.parse import urlparse
from collections import deque
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import kitconfig  # noqa: E402
CFG = kitconfig.load()
UDP_PORT, HTTP_PORT = int(CFG["host"]["udp_port"]), int(CFG["host"]["http_port"])
WEB = os.path.join(HERE, "web")
DATA = os.path.join(HERE, "..", "data"); os.makedirs(DATA, exist_ok=True)
LAYOUT = os.path.join(DATA, "layout.json")        # where router + boards sit on the room map
CALIB = os.path.join(DATA, "calib.npz")           # room-map calibration windows
N_RAW = 64           # first 64 entries = the legacy LTF block, present in every packet type
# 802.11 20 MHz: index 0 (DC) and 27-37 (guard band) carry nothing; the other 52 are real subcarriers
VALID = np.r_[1:27, 38:64]
N_SC = len(VALID)
WINDOW = 500         # frames kept per node (~10 s at 50 Hz)
CSI_MAGIC, VITALS_MAGIC = 0xC5110001, 0xC5110002
# Without a MAC filter each board measures every packet it overhears. Measured on this network:
# 404-byte frames (HT, the router answering the board's pings) correlate 0.98 frame-to-frame with a
# steady RSSI; 276-byte frames are a mix of other senders (0.71-0.89, RSSI +-6 dB). Use only the former.
USE_SIZES = {404}
# Firmware with the local "board-to-board links" patch tags each frame with its transmitter:
# byte 19 bit 7 set => 6-byte transmitter MAC after the I/Q. Senders we keep (the firmware also
# drops everything else): the router and the three boards. Links are named "<tx>><rx>", e.g. "R>1", "2>3".
TX_FLAG = 0x80
SENDERS = kitconfig.senders(CFG)   # MAC -> "R" (router) / "1", "2", … (boards), from config.yaml


class Link:
    """One transmitter -> receiver path. Within a link only the most common frame length is used
    (different PPDU types from the same sender have different spectral shapes)."""

    def __init__(s, key):
        s.key = key
        s.amp = deque(maxlen=WINDOW); s.t = deque(maxlen=WINDOW)
        s.sizes = {}; s.arrivals = deque(maxlen=400); s.rssi = None
        s.lock = threading.Lock()

    def add(s, size, amp, now, rssi):
        with s.lock:
            s.sizes[size] = s.sizes.get(size, 0) + 1
            if sum(s.sizes.values()) > 20 and size != max(s.sizes, key=s.sizes.get):
                return False
            s.amp.append(amp); s.t.append(now); s.arrivals.append(now); s.rssi = rssi
            return True


links: dict[str, Link] = {}


class Node:
    def __init__(s, nid):
        s.id, s.ip = nid, None
        s.amp = deque(maxlen=WINDOW)          # float32[N_SC] per frame
        s.t = deque(maxlen=WINDOW)
        s.arrivals = deque(maxlen=400)        # arrival times, for packets/s
        s.rssi = s.noise = s.freq = None
        s.last_seq = None; s.received = 0; s.lost = 0
        s.n_sc_raw = 0
        s.other = 0                           # frames from other senders, ignored
        s.vitals = {}
        s.lock = threading.Lock()


nodes: dict[int, Node] = {}


def parse(d, ip):
    if len(d) < 4:
        return
    magic = struct.unpack_from("<I", d)[0]
    now = time.time()
    if magic == CSI_MAGIC and len(d) >= 20:
        nid, nant = d[4], d[5]
        n_sc, freq, seq = struct.unpack_from("<HII", d, 6)
        rssi, noise = struct.unpack_from("<bb", d, 16)
        tagged = bool(d[19] & TX_FLAG) and len(d) >= 26
        raw = d[20:-6] if tagged else d[20:]
        src = SENDERS.get(":".join(f"{b:02x}" for b in d[-6:])) if tagged else ("R" if len(d) in USE_SIZES else None)
        iq = np.frombuffer(raw, np.int8).astype(np.float32)
        if len(iq) < 2 * N_RAW:
            return
        im, re = iq[0:2 * N_RAW:2], iq[1:2 * N_RAW:2]   # ESP-IDF order: imaginary, real
        amp = np.sqrt(im * im + re * re)[VALID]
        # the receiver's automatic gain jumps packet to packet; keep the SHAPE across bands (scaled to ~20)
        amp = amp / (amp.mean() + 1e-6) * 20.0
        n = nodes.setdefault(nid, Node(nid))
        if src is None or src == str(nid):
            with n.lock:
                n.other += 1
            return
        key = f"{src}>{nid}"
        kept = links.setdefault(key, Link(key)).add(len(raw), amp, now, rssi)
        if src != "R" or not kept:
            with n.lock:
                n.ip = ip
                if src == "R": n.other += 1
            return
        with n.lock:   # the router link also feeds the per-board card on the signals page
            n.ip, n.rssi, n.noise, n.freq, n.n_sc_raw = ip, rssi, noise, freq, len(iq) // 2
            n.received += 1
            n.amp.append(amp); n.t.append(now); n.arrivals.append(now)
    elif magic == VITALS_MAGIC and len(d) >= 32:
        nid, flags, br = struct.unpack_from("<BBH", d, 4)
        motion, presence = struct.unpack_from("<ff", d, 16)
        n = nodes.setdefault(nid, Node(nid))
        with n.lock:
            n.ip = ip
            n.vitals = {"presence": bool(flags & 1), "fall": bool(flags & 2), "motion": bool(flags & 4),
                        "breathing_bpm": br / 100.0, "motion_energy": motion, "presence_score": presence,
                        "n_persons": d[13], "t": now}


def udp_loop():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 22)
    s.bind(("0.0.0.0", UDP_PORT))
    while True:
        d, a = s.recvfrom(4096)
        try:
            parse(d, a[0])
        except Exception as e:  # a malformed packet must not kill the listener
            print("parse error:", e, flush=True)


def snapshot(since):
    """Frames newer than `since` per node, quantised to uint8, plus stats."""
    out, now = {}, time.time()
    for nid, n in sorted(nodes.items()):
        with n.lock:
            t = np.array(n.t); A = np.array(n.amp) if n.amp else np.zeros((0, N_SC), np.float32)
            arr = [x for x in n.arrivals if now - x < 2.0]
            vit = dict(n.vitals)
            info = {"ip": n.ip, "rssi": n.rssi, "noise": n.noise, "freq": n.freq, "n_sc_raw": n.n_sc_raw,
                    "pps": len(arr) / 2.0, "used": n.received / max(1, n.received + n.other),
                    "age": now - t[-1] if len(t) else None}
        new = A[t > since] if len(t) else A
        # motion: how much each band fluctuates over the last second, relative to its level
        recent = A[t > now - 1.0] if len(t) else A
        motion = float((recent.std(0) / (recent.mean(0) + 1e-3)).mean()) if len(recent) > 5 else 0.0
        out[nid] = {**info, "motion": motion, "vitals": vit,
                    "frames": np.clip(new, 0, 63).astype(np.uint8).tolist()}   # amplitudes rarely exceed ~60
    return out


# --------------------------------------------------------------------------- localisation
# A person standing somewhere changes each router->board channel in a repeatable way: the SHAPE
# across the 52 bands (multipath) and how much it fluctuates. One feature vector per 0.5 s:
# for every board, the mean band shape and the per-band std over the last 1.5 s.
FEAT_WIN, FEAT_HOP, MIN_FRAMES = 1.5, 0.5, 4


def node_feature(n, now):
    with n.lock:
        t = np.array(n.t); A = np.array(n.amp) if n.amp else None
    if A is None:
        return None
    w = A[t > now - FEAT_WIN]
    if len(w) < MIN_FRAMES:
        return None
    return np.concatenate([w.mean(0), w.std(0)])


def active_links(now, min_pps=5.0):
    out = []
    for k, l in sorted(links.items()):
        with l.lock:
            n = sum(1 for x in l.arrivals if now - x < 3.0)
        if n / 3.0 >= min_pps:
            out.append(k)
    return out


_last_feat = {}   # link -> (time, feature): a briefly stalled link reuses its last reading


def feature(now, ids, max_stale=3.0):
    parts = []
    for i in ids:
        f = node_feature(links[i], now) if i in links else None
        if f is not None:
            _last_feat[i] = (now, f)
        elif i in _last_feat and now - _last_feat[i][0] < max_stale:
            f = _last_feat[i][1]
        else:
            return None
        parts.append(f)
    return np.concatenate(parts).astype(np.float32)


class Calib:
    """Labelled windows: label 'empty' or a spot id; spots carry plan coordinates."""

    def __init__(s, path=CALIB):
        s.path = path
        s.X, s.y, s.r, s.spots, s.ids = [], [], [], {}, None
        s.recent = deque(maxlen=6)    # last 3 s of kNN probabilities, for a steadier estimate
        s.rec = None                  # {"label", "until", "n"}
        s.model = None
        s.lock = threading.Lock()
        if os.path.exists(s.path):
            d = np.load(s.path, allow_pickle=True)
            s.X, s.y = list(d["X"]), list(d["y"])
            s.r = list(d["r"]) if "r" in d.files else [0] * len(s.y)
            s.spots = json.loads(str(d["spots"])); s.ids = [str(i) for i in d["ids"]]
            if s.ids and not all(">" in i for i in s.ids):   # calibrated before links existed: R>board
                s.ids = [f"R>{i}" for i in s.ids]
            s.train()

    def save(s):
        np.savez(s.path, X=np.array(s.X), y=np.array(s.y), r=np.array(s.r, dtype=np.int32), spots=json.dumps(s.spots), ids=np.array(s.ids or [], dtype=str))

    def start(s, label, seconds, spot=None, rnd=0):
        with s.lock:
            ids = active_links(time.time())
            if not ids:
                return "no links are streaming"
            # links are chosen at the first recording (>= 5 pkt/s); later recordings only need them alive
            if s.ids and not set(s.ids) <= set(active_links(time.time(), min_pps=0.5)):
                return f"links changed (calibrated {s.ids}, now {ids}); clear calibration first"
            s.ids = s.ids or ids
            if spot:
                s.spots[label] = spot
            s.rec = {"label": label, "until": time.time() + seconds, "n": 0, "round": int(rnd)}
        return None

    def tick(s, now):
        with s.lock:
            if not s.rec:
                return
            if now > s.rec["until"]:
                s.rec = None; s.save(); s.train(); return
            f = feature(now, s.ids)
            if f is not None:
                s.X.append(f); s.y.append(s.rec["label"]); s.r.append(s.rec["round"]); s.rec["n"] += 1

    def clear(s, label=None):
        with s.lock:
            if label:
                keep = [i for i, l in enumerate(s.y) if l != label]
                s.X = [s.X[i] for i in keep]; s.y = [s.y[i] for i in keep]; s.r = [s.r[i] for i in keep]; s.spots.pop(label, None)
            else:
                s.X, s.y, s.r, s.spots, s.ids, s.model = [], [], [], {}, None, None
            s.save(); s.train()

    def train(s):
        """k-nearest-neighbours on standardised windows, plus an accuracy you can trust:
        with >= 2 calibration rounds, train on the other rounds and test on each round in turn
        (every spot is then recognised at a different moment from when it was learned).
        With a single round, fall back to a time split inside each recording and mark it
        "same_session": that number is optimistic (time drift is learned along with position)."""
        labels = sorted(set(s.y))
        if len(labels) < 2:
            s.model = None; return
        X = np.array(s.X); y = np.array(s.y); r = np.array(s.r)
        mu, sd = X.mean(0), X.std(0) + 1e-6
        Z = (X - mu) / sd
        rounds = sorted(set(r.tolist()))
        acc, kind, per = None, None, {}
        if len(rounds) >= 2:
            hits = []
            for rr in rounds:
                tr, te = np.where(r != rr)[0], np.where(r == rr)[0]
                if len(set(y[tr])) < 2 or not len(te):
                    continue
                p = np.array([s._knn(Z[tr], y[tr], z)[0] for z in Z[te]])
                hits += list(zip(y[te], p == y[te]))
            if hits:
                acc, kind = float(np.mean([h for _, h in hits])), "across_rounds"
                for l in labels:
                    hl = [h for t, h in hits if t == l]
                    per[l] = float(np.mean(hl)) if hl else None
        else:
            tr, te = [], []
            for l in labels:
                idx = np.where(y == l)[0]; k = max(1, int(len(idx) * 0.7))
                tr += list(idx[:k]); te += list(idx[k:])
            if te:
                pred = [s._knn(Z[tr], y[tr], z)[0] for z in Z[te]]
                acc, kind = float(np.mean(np.array(pred) == y[te])), "same_session"
        s.model = {"mu": mu, "sd": sd, "Z": Z, "y": y, "labels": labels, "acc": acc, "acc_kind": kind,
                   "per_spot": per, "rounds": len(rounds), "counts": {l: int((y == l).sum()) for l in labels}}

    @staticmethod
    def _knn(Z, y, z, k=7):
        d = np.linalg.norm(Z - z, axis=1); nn = np.argsort(d)[:k]
        vals, cnt = np.unique(y[nn], return_counts=True)
        probs = dict(zip(vals.tolist(), (cnt / cnt.sum()).tolist()))
        return max(probs, key=probs.get), probs

    def estimate(s, now):
        m = s.model
        if not m or not s.ids:
            return None
        f = feature(now, s.ids)
        if f is None:
            return {"status": "waiting for all boards"}
        _, p_now = s._knn(m["Z"], m["y"], (f - m["mu"]) / m["sd"])
        s.recent.append(p_now)
        probs = {l: float(np.mean([p.get(l, 0.0) for p in s.recent])) for l in m["labels"]}
        best = max(probs, key=probs.get)
        # position: probability-weighted average over spot coordinates (empty excluded)
        sp = {l: p for l, p in probs.items() if l in s.spots}
        pos = None
        if sp and best != "empty":
            w = sum(sp.values())
            pos = [sum(s.spots[l]["x"] * p for l, p in sp.items()) / w, sum(s.spots[l]["y"] * p for l, p in sp.items()) / w]
        return {"status": "ok" if probs[best] >= 0.55 else "unsure", "best": best, "confidence": probs[best], "probs": probs, "pos": pos}

    def summary(s):
        m = s.model
        return {"spots": s.spots, "ids": s.ids, "counts": {l: s.y.count(l) for l in set(s.y)},
                "recording": {"label": s.rec["label"], "left": max(0.0, s.rec["until"] - time.time()), "n": s.rec["n"]} if s.rec else None,
                "acc": m["acc"] if m else None, "acc_kind": m["acc_kind"] if m else None,
                "per_spot": m["per_spot"] if m else {}, "rounds": m["rounds"] if m else 0, "trained": bool(m)}


calib = None     # where in the room (labels = spots on the floor plan)


# --------------------------------------------------------------------------- movement (camera-taught)
# The browser runs MediaPipe Pose on the Mac's camera and posts a movement label derived from the
# skeleton ~10x/s (it never sends video). While recording, every 0.5 s the WiFi feature over all
# links is stored with the camera's majority label over the same 1.5 s. The WiFi model never sees
# the camera at prediction time.
MOVE = os.path.join(DATA, "movement.npz")
MOVE_WIN = 1.5


def move_feature(now, ids):
    """Per link: mean band shape, per-band std, and mean frame-to-frame change (motion rhythm)."""
    parts = []
    for i in ids:
        l = links.get(i)
        if l is None:
            return None
        with l.lock:
            t = np.array(l.t); A = np.array(l.amp) if l.amp else None
        w = A[t > now - MOVE_WIN] if A is not None else None
        if w is None or len(w) < MIN_FRAMES:
            if i in _last_move and now - _last_move[i][0] < 3.0:
                parts.append(_last_move[i][1]); continue
            return None
        f = np.concatenate([w.mean(0), w.std(0), np.abs(np.diff(w, axis=0)).mean(0)])
        _last_move[i] = (now, f); parts.append(f)
    return np.concatenate(parts).astype(np.float32)


_last_move = {}


class Camera:
    def __init__(s):
        s.hist = deque(maxlen=200)   # (time, label)
        s.tabs = {}                  # page id -> last time it sent a label
        s.lock = threading.Lock()

    def add(s, label, tab=None):
        with s.lock:
            s.hist.append((time.time(), label)); s.tabs[tab] = time.time()

    def tabs_active(s, now):
        with s.lock:
            return sum(1 for t in s.tabs.values() if now - t < 2.0)

    def window_label(s, now, win=MOVE_WIN, agree=0.6):
        with s.lock:
            L = [l for t, l in s.hist if now - t < win]
        if len(L) < 5:
            return None
        lab = max(set(L), key=L.count)
        return lab if L.count(lab) / len(L) >= agree and lab != "unsure" else None

    def latest(s, now):
        with s.lock:
            return (s.hist[-1][1], now - s.hist[-1][0]) if s.hist else (None, None)


class Movement:
    """Samples = (WiFi feature, camera label, session 'train'|'test', round)."""

    def __init__(s):
        s.X, s.y, s.sess, s.r, s.ids = [], [], [], [], None
        s.rec = None; s.model = None; s.recent = deque(maxlen=6)
        s.agree = deque(maxlen=120)  # (wifi == camera) over the last ~60 s while the camera sees something
        s.lock = threading.Lock()
        if os.path.exists(MOVE):
            d = np.load(MOVE, allow_pickle=True)
            s.X, s.y, s.sess, s.r = list(d["X"]), list(d["y"]), list(d["sess"]), list(d["r"])
            s.ids = [str(i) for i in d["ids"]] or None
            s.train()

    def save(s):
        np.savez(MOVE, X=np.array(s.X), y=np.array(s.y), sess=np.array(s.sess), r=np.array(s.r, dtype=np.int32),
                 ids=np.array(s.ids or [], dtype=str))

    def start(s, on, session="train", rnd=1):
        with s.lock:
            if not on:
                s.rec = None; s.save(); s.train(); return None
            ids = active_links(time.time())
            if not ids:
                return "no links are streaming"
            # links are chosen at the first recording (>= 5 pkt/s); later recordings only need them alive
            if s.ids and not set(s.ids) <= set(active_links(time.time(), min_pps=0.5)):
                return f"links changed (recorded with {s.ids}, now {ids}); clear first"
            s.ids = s.ids or ids
            s.rec = {"session": session, "round": int(rnd), "n": 0, "skipped": 0, "no_label": 0, "no_wifi": 0}
        return None

    def tick(s, now, cam):
        with s.lock:
            if not s.rec or not s.ids:
                return
            lab = cam.window_label(now)
            f = move_feature(now, s.ids)
            if lab is None or f is None:
                s.rec["skipped"] += 1
                if lab is None: s.rec["no_label"] += 1
                if f is None: s.rec["no_wifi"] += 1
                return
            s.X.append(f); s.y.append(lab); s.sess.append(s.rec["session"]); s.r.append(s.rec["round"]); s.rec["n"] += 1
            if s.rec["n"] % 10 == 0: s.save()

    def clear(s, session=None):
        with s.lock:
            if session:
                keep = [i for i, x in enumerate(s.sess) if x != session]
                s.X = [s.X[i] for i in keep]; s.y = [s.y[i] for i in keep]; s.sess = [s.sess[i] for i in keep]; s.r = [s.r[i] for i in keep]
                if not s.X: s.ids = None
            else:
                s.X, s.y, s.sess, s.r, s.ids = [], [], [], [], None
            s.rec = None   # clearing mid-recording used to leave rec on with no links -> crashed the loop
            s.save(); s.train()

    def train(s):
        X = np.array(s.X); y = np.array(s.y); sess = np.array(s.sess); r = np.array(s.r)
        tr = sess == "train"
        if tr.sum() < 10 or len(set(y[tr])) < 2:
            s.model = None; return
        mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-6
        Z = (X - mu) / sd
        knn = Calib._knn
        # honest score 1: separate test session (recorded at a different time)
        test = None
        if (~tr).any():
            p = np.array([knn(Z[tr], y[tr], z)[0] for z in Z[~tr]])
            test = {"acc": float((p == y[~tr]).mean()), "n": int((~tr).sum()),
                    "per": {l: float((p[y[~tr] == l] == l).mean()) for l in sorted(set(y[~tr]))},
                    "confusion": {l: {q: int(((y[~tr] == l) & (p == q)).sum()) for q in sorted(set(y))} for l in sorted(set(y[~tr]))}}
        # honest score 2: leave one training round out
        rounds = sorted(set(r[tr].tolist())); lor = None
        if len(rounds) >= 2:
            hits = []
            for rr in rounds:
                a, b = tr & (r != rr), tr & (r == rr)
                if len(set(y[a])) >= 2 and b.any():
                    hits += list(np.array([knn(Z[a], y[a], z)[0] for z in Z[b]]) == y[b])
            lor = float(np.mean(hits)) if hits else None
        s.model = {"mu": mu, "sd": sd, "Z": Z[tr], "y": y[tr], "labels": sorted(set(y[tr])), "test": test, "lor": lor}

    def estimate(s, now, cam):
        m = s.model
        if not m or not s.ids:
            return None
        f = move_feature(now, s.ids)
        if f is None:
            return {"status": "waiting for links"}
        _, p_now = Calib._knn(m["Z"], m["y"], (f - m["mu"]) / m["sd"])
        s.recent.append(p_now)
        probs = {l: float(np.mean([p.get(l, 0.0) for p in s.recent])) for l in m["labels"]}
        best = max(probs, key=probs.get)
        lab = cam.window_label(now)
        if lab is not None and not s.rec:
            s.agree.append(best == lab)
        return {"status": "ok" if probs[best] >= 0.5 else "unsure", "best": best, "confidence": probs[best], "probs": probs,
                "agree": float(np.mean(s.agree)) if len(s.agree) >= 10 else None}

    def summary(s):
        c = {}
        for se, l in zip(s.sess, s.y):
            c.setdefault(se, {}); c[se][l] = c[se].get(l, 0) + 1
        m = s.model or {}
        return {"counts": c, "ids": s.ids, "recording": dict(s.rec) if s.rec else None,
                "test": m.get("test"), "lor": m.get("lor"), "trained": bool(s.model)}


camera = Camera()
movement = None


def calib_loop():
    """Records calibration/training windows every 0.5 s. Must never die: an exception here used to
    stop all recording silently while the page kept counting down."""
    while True:
        now = time.time()
        for name, fn in (("room calibration", lambda: calib.tick(now)), ("movement", lambda: movement.tick(now, camera))):
            try:
                fn()
            except Exception as e:
                print(f"{name} tick error: {e!r}", flush=True)
        time.sleep(FEAT_HOP)


def read_json(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def link_stats(now):
    out = {}
    for k, l in sorted(links.items()):
        with l.lock:
            t = np.array(l.t); A = np.array(l.amp) if l.amp else np.zeros((0, N_SC))
            pps = sum(1 for x in l.arrivals if now - x < 2.0) / 2.0
        rec = A[t > now - 1.0] if len(t) else A
        motion = float((rec.std(0) / (rec.mean(0) + 1e-3)).mean()) if len(rec) > 4 else 0.0
        out[k] = {"pps": pps, "motion": motion, "rssi": l.rssi, "age": now - t[-1] if len(t) else None}
    return out


class H(SimpleHTTPRequestHandler):
    # MediaPipe (movement page) ships ES modules + WebAssembly; browsers need the right types
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript", ".wasm": "application/wasm"}

    def __init__(s, *a, **k):
        super().__init__(*a, directory=WEB, **k)

    def log_message(s, *a):
        pass

    def do_GET(s):
        if s.path.startswith("/stream"):
            s.send_response(200)
            s.send_header("Content-Type", "text/event-stream"); s.send_header("Cache-Control", "no-cache")
            s.end_headers()
            since = time.time() - 10
            try:
                while True:
                    now = time.time()
                    msg = {"t": now, "nodes": snapshot(since), "links": link_stats(now), "calib": calib.summary(), "estimate": calib.estimate(now),
                           "move": movement.summary(), "move_est": movement.estimate(now, camera),
                           "cam": {**dict(zip(("label", "age"), camera.latest(now))), "tabs": camera.tabs_active(now)}}
                    s.wfile.write(f"data: {json.dumps(msg)}\n\n".encode())
                    s.wfile.flush(); since = now
                    time.sleep(1 / 15)
            except (BrokenPipeError, ConnectionResetError):
                return
        if s.path.startswith("/api/layout"):
            return s.send_json(read_json(LAYOUT, {}))
        if s.path.startswith("/api/config"):   # what the pages need: board colours, room size, optional plan
            room = dict(CFG.get("room") or {}); plan = room.get("plan")
            if plan: room["plan"] = {**plan, "url": "/plan/" + os.path.basename(plan["image"])}
            return s.send_json({"boards": [{"id": b["id"], "colour": b["colour"]} for b in CFG["boards"]], "room": room})
        if s.path.startswith("/plan/"):
            name = os.path.basename(s.path); ext = name.rsplit(".", 1)[-1].lower()
            return s.send_file(os.path.join(DATA, name), {"png": "image/png", "svg": "image/svg+xml"}.get(ext, "image/jpeg"))
        return super().do_GET()

    def send_json(s, obj, code=200):
        b = json.dumps(obj).encode()
        s.send_response(code); s.send_header("Content-Type", "application/json"); s.send_header("Content-Length", str(len(b)))
        s.end_headers(); s.wfile.write(b)

    def send_file(s, path, ctype):
        if not os.path.exists(path):
            return s.send_error(404)
        b = open(path, "rb").read()
        s.send_response(200); s.send_header("Content-Type", ctype); s.send_header("Content-Length", str(len(b)))
        s.end_headers(); s.wfile.write(b)

    def do_POST(s):
        body = json.loads(s.rfile.read(int(s.headers.get("Content-Length", 0))) or b"{}")
        path = urlparse(s.path).path
        if path == "/api/layout":
            json.dump(body, open(LAYOUT, "w"), indent=1); return s.send_json({"ok": True})
        if path == "/api/pose":
            camera.add(str(body.get("label", "unsure")), str(body.get("tab"))); return s.send_json({"ok": True})
        if path == "/api/move/rec":
            err = movement.start(bool(body.get("on")), body.get("session", "train"), body.get("round", 1))
            return s.send_json({"ok": not err, "error": err}, 400 if err else 200)
        if path == "/api/move/clear":
            movement.clear(body.get("session")); return s.send_json({"ok": True})
        if path == "/api/calib/record":
            err = calib.start(str(body["label"]), float(body.get("seconds", 15)), body.get("spot"), body.get("round", 0))
            return s.send_json({"ok": not err, "error": err}, 400 if err else 200)
        if path == "/api/calib/clear":
            calib.clear(body.get("label")); return s.send_json({"ok": True})
        return s.send_error(404)


if __name__ == "__main__":
    threading.Thread(target=udp_loop, daemon=True).start()
    calib = Calib()
    movement = Movement()
    threading.Thread(target=calib_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), H)
    print(f"listening for boards on UDP {UDP_PORT} (this computer: {CFG['host']['ip']}) · open http://127.0.0.1:{HTTP_PORT}", flush=True)
    srv.serve_forever()
