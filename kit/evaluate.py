"""Honest numbers for your movement run (data/movement.npz).

  python kit/evaluate.py

- test accuracy: a model trained on the training session predicts the separate test session,
  judged by the camera's labels (never by frames the model trained on)
- per movement + confusion table
- which WiFi links carry the signal (router links vs board-to-board, each link alone)
- a "motion only" baseline: is the model just measuring how much you move?
Same k-nearest-neighbour model (k = 7, standardised features) as the live server.
"""
import os, sys, collections
import numpy as np

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "movement.npz")
N_SC = 52   # usable subcarriers per link; per link the feature is [mean shape | std | mean frame-to-frame change]


def knn(Ztr, ytr, z, k=7):
    nn = np.argsort(np.linalg.norm(Ztr - z, axis=1))[:k]
    v, c = np.unique(ytr[nn], return_counts=True)
    return v[c.argmax()]


def score(Z, y, tr, te, cols=None):
    A = Z if cols is None else Z[:, cols]
    p = np.array([knn(A[tr], y[tr], z) for z in A[te]])
    return float((p == y[te]).mean()), p


if __name__ == "__main__":
    if not os.path.exists(DATA):
        sys.exit("no data/movement.npz yet — record a training and a test session on the movement page")
    d = np.load(DATA, allow_pickle=True)
    X, y, sess, ids = d["X"].astype(float), d["y"], d["sess"], [str(i) for i in d["ids"]]
    tr, te = sess == "train", sess == "test"
    print(f"links: {', '.join(ids)}")
    for name, m in (("training", tr), ("test", te)):
        print(f"{name}: {m.sum()} windows ·", ", ".join(f"{k} {v}" for k, v in sorted(collections.Counter(y[m].tolist()).items())))
    if not te.any():
        sys.exit("\nrecord a test session to get an honest score")
    mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-6
    Z = (X - mu) / sd
    labels = sorted(set(y.tolist()))
    acc, p = score(Z, y, tr, te)
    print(f"\nTEST SESSION: {100 * acc:.1f}%  (guessing: {100 / len(labels):.1f}%, {te.sum()} windows)\n")
    w = max(len(l) for l in labels)
    print(" " * (w + 2) + "  ".join(l[:6].rjust(6) for l in labels) + "   correct")
    for l in labels:
        m = y[te] == l
        if m.any():
            print(f"{l:>{w}}  " + "  ".join(str(int(((p == q) & m).sum())).rjust(6) for q in labels) + f"   {100 * (p[m] == l).mean():5.0f}%")
    print("(rows: what the camera saw · columns: what WiFi guessed)")
    F = X.shape[1] // len(ids)
    cols = lambda L: np.concatenate([np.arange(i * F, (i + 1) * F) for i in L])
    R = [i for i, k in enumerate(ids) if k.startswith("R>")]; B = [i for i, k in enumerate(ids) if not k.startswith("R>")]
    print("\nlinks:")
    if R and B:
        print(f"  router links only ({len(R)}): {100 * score(Z, y, tr, te, cols(R))[0]:.1f}%   "
              f"board-to-board only ({len(B)}): {100 * score(Z, y, tr, te, cols(B))[0]:.1f}%   all: {100 * acc:.1f}%")
    for i, k in enumerate(ids):
        print(f"  {k} alone: {100 * score(Z, y, tr, te, cols([i]))[0]:.0f}%")
    motion = np.concatenate([np.arange(i * F + N_SC, (i + 1) * F) for i in range(len(ids))])
    print(f"\nmotion only (no band shape): {100 * score(Z, y, tr, te, motion)[0]:.1f}% — the gap to {100 * acc:.1f}% is what the signal's shape adds")
