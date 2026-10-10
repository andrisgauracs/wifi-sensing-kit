# Third-party components

| Component | Where | Licence |
|---|---|---|
| RuView `esp32-csi-node` firmware v0.8.8 (ruvnet/RuView) | downloaded by `firmware/build.py`; this repo ships `firmware/ruview-v0.8.8-kit.patch` and uses its `provision.py` | MIT |
| MediaPipe Tasks Vision 0.10.14 + Pose Landmarker (full) model (Google) | downloaded by `install.py` into `kit/web/vendor/mediapipe/` | Apache-2.0 |
| Inter (Rasmus Andersson), JetBrains Mono (JetBrains) | `kit/web/assets/fonts/` | SIL Open Font License 1.1 |
| ESP-IDF (Espressif) | installed separately | Apache-2.0 |

The patch modifies RuView's `main/main.c` (LED colour per board from `kit_config.h`) and
`main/csi_collector.c` (keep only router/board frames; append the transmitter MAC to each frame).
