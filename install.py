"""One-time setup: Python packages + the in-browser pose model.

  python3 -m venv .venv && .venv/bin/python install.py

Downloads Google MediaPipe Tasks Vision 0.10.14 (Apache-2.0) and the pose_landmarker_full model
into kit/web/vendor/mediapipe/ (~28 MB), so the movement page runs fully offline afterwards.
"""
import os, subprocess, sys, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(ROOT, "kit", "web", "vendor", "mediapipe")
CDN = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14"
FILES = {
    "vision_bundle.mjs": f"{CDN}/vision_bundle.mjs",
    "wasm/vision_wasm_internal.js": f"{CDN}/wasm/vision_wasm_internal.js",
    "wasm/vision_wasm_internal.wasm": f"{CDN}/wasm/vision_wasm_internal.wasm",
    "wasm/vision_wasm_nosimd_internal.js": f"{CDN}/wasm/vision_wasm_nosimd_internal.js",
    "wasm/vision_wasm_nosimd_internal.wasm": f"{CDN}/wasm/vision_wasm_nosimd_internal.wasm",
    "pose_landmarker_full.task": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task",
}

if __name__ == "__main__":
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", os.path.join(ROOT, "requirements.txt")], check=True)
    for rel, url in FILES.items():
        dst = os.path.join(VENDOR, rel)
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        print("downloading", rel, flush=True)
        urllib.request.urlretrieve(url, dst)
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    if not os.path.exists(os.path.join(ROOT, "config.yaml")):
        print("\nnext: cp config.example.yaml config.yaml, then follow the README (docs/setup.md)")
    else:
        print("\nsetup done")
