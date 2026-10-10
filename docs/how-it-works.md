# How it works

## What the boards measure

Every WiFi packet is spread over many narrow frequency bands (subcarriers). The receiver measures how
each band arrived — its strength and phase — to decode the data. That measurement is **Channel State
Information (CSI)**. A person in the room adds and blocks reflection paths, so CSI changes when you move.

Each ESP32-S3 has one antenna and works at 2.4 GHz. Per received packet it reports 64 subcarrier
values, of which 52 carry signal (the DC bin and the guard band are empty). The firmware pings the
router 50 times a second to make sure there is always traffic to measure, and streams every CSI
measurement to your computer over UDP.

## Links

A **link** is one transmitter → receiver pair, written `R>1` (router to board 1) or `2>3`
(board 2 to board 3). With the kit's patch every frame carries its transmitter's MAC, and the board
ignores frames from anyone other than the router and the other boards. Three boards give up to nine
links: three from the router, six between boards. The server keeps only links that stream at least
5 frames a second when you start recording.

Each board can only report ~50 measurements a second in total, shared between its links, first come
first served. A board close to the router may spend most of its budget on the router.

## Features

Every 0.5 s, for each link, over the last 1.5 s:
- the **mean shape** across the 52 bands (where you are and how you're standing changes the multipath),
- the per-band **spread** (how much it's fluctuating),
- the mean **frame-to-frame change** (the rhythm of the movement).

Each frame is first divided by its own average level, because the receiver's automatic gain jumps
from packet to packet.

## The camera as teacher

The movement page runs MediaPipe Pose in the browser and turns the skeleton into a movement name
with a few rules over the last 1.5–2.5 s:

| Movement | Rule |
|---|---|
| Walking | hips move sideways, or you get bigger/smaller in the image (hip width in pixels) |
| Spinning | shoulder width swings wide/narrow ≥ 3 times while you stay in place |
| Squatting | thighs look shorter than your own standing thighs, while you move up and down |
| Sitting | thighs short (or knee clearly bent) and still |
| Jumping | hips bounce, legs straight |
| Waving | wrists above shoulders |
| Standing | still, at least one knee visible |
| Nobody | no confident skeleton |

These came from measuring real footage: facing the camera, a squat barely changes the knee angle *in
the image* (the knees bend toward the lens), sitting side-on hides the far leg and both ankles, and
the pose model never reports your face as hidden — even from behind. On one recorded session the
rules label 77% of moments correctly. Moments the camera isn't sure about are not recorded.

WiFi never sees the camera at prediction time: a k-nearest-neighbour model (k = 7) on standardised
features predicts the movement from WiFi alone, averaged over the last 3 s.

## Why the scores are honest

- The **test session** is recorded separately, after training. Scoring on frames picked at random
  from the training recordings would let the model "recognise" a moment it saw 0.1 s earlier — a
  common trap in WiFi-sensing benchmarks.
- The room map scores calibration by **leaving one round out**: each round is predicted by a model
  that only saw the other rounds.

## Limits

- **One antenna, three links per board, 2.4 GHz.** Locating a person precisely needs many links or
  multi-antenna receivers; three boards give "which part of the room", not coordinates. Walking
  anywhere disturbs every link — reflections, not straight lines.
- **No skeleton.** RuView's own README reports 3% for its live single-board pose model.
- **Locked to the setup.** Move a board, the router or big furniture, add a person — retrain.
