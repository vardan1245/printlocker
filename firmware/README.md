# PrintLocker controller firmware (ESP32)

The ESP32 runs the lift, the pusher, the compartment locks and the sensors. The Raspberry Pi tells it
what to do over USB serial, using the JSON-lines protocol documented in
[`kiosk-agent/agent/twin.py`](../kiosk-agent/agent/twin.py).

```
src/core/      protocol + delivery state machine (plain C++17, no hardware calls)
src/esp32/     the board: TMC2209 drivers, MCP23017 expanders, limit switches, sheet counter, LEDs
src/native/    a simulated board, so the same core runs on a PC
```

The core is the same code on the board and on the PC. The PC build is tested in CI with the controller
acceptance tests (`kiosk-agent/tests/test_controller_protocol.py`), the same tests the twin passes. The
ESP32 build is compiled in CI, and `firmware.bin` can be downloaded from the workflow run.

| Command | What it does |
|---|---|
| `make test` | PC build + acceptance tests (no board needed) |
| `pio run -e esp32dev -t upload` | build and flash the board over USB |
| `PL_CONFORMANCE_PORT=/dev/ttyUSB0 pytest tests/test_controller_protocol.py` (in `kiosk-agent/`) | acceptance tests against the real board |

## Bench bring-up: the one-compartment prototype (step 5)

Goal: prove the lift, pusher, one door and the electronics on a bench rig before any metal is ordered.
Each stage has a pass check; don't go on until it passes.

### Rig
- One lift: two 2040 extrusions with MGN12 rails, two T8×8 lead screws, two NEMA17, platform with guides.
  A 600 mm stroke is enough (receive position plus 3 compartment levels).
- One pusher on the platform (NEMA17 + GT2 belt) and one compartment shelf with rear flap, door,
  solenoid lock, reed switch and IR paper sensor. Plywood is fine for the bench.
- Electronics as in [`hardware/README.md`](../hardware/README.md): ESP32 DevKit, 3× TMC2209, 2× MCP23017,
  MOSFET board, LRS-150-24 PSU, 12 V buck, limit switches on GPIO 34/35/39, platform IR sensor on GPIO 36.

### 1. Power and I/O (motors unplugged)
1. Flash: `pio run -e esp32dev -t upload`, then open `pio device monitor`. Pass: `{"event":"boot","version":"0.1.0"}`.
2. Send `{"id":1,"cmd":"config","locks":[{"lock":1,"column":1,"slot":1}]}`. The firmware homes at this
   point; with motors unplugged you get `{"event":"homing_failed"}`, which is expected here.
3. `{"id":2,"cmd":"sensors"}`, then open and close the door, cover and uncover the paper sensor.
   Pass: `door_closed` and `paper` follow.
4. `{"id":3,"cmd":"unlock","lock":1}`. Pass: the lock clicks for 0.5 s and the solenoid is cold afterwards.
5. Wave a sheet through the platform sensor 10 times, then `{"id":4,"cmd":"status"}`. Pass: `platform_sheets` is 10.

### 2. Motors and calibration
1. Plug the motors in, power on. Pass: homing drives the lift down to the bottom switch and the pusher
   back to its home switch.
2. Mark the carriage, command a delivery to slot 1, and measure the lift travel against the expected
   170 mm. Correct `LIFT_STEPS_PER_MM` in `src/esp32/main_esp32.cpp` by `expected / measured`. Do the
   same for the 320 mm pusher stroke (`PUSH_STEPS_PER_MM`).
3. Set the shelf height on the rig to match `firstFloorMm` (516 mm above the receive position's
   reference), or change `Geometry` in `src/core/controller.h` to the rig's real numbers.
4. Pass: the platform stops within ±1 mm of the shelf, 10 times in a row.

### 3. StallGuard
1. Run a delivery and hold the pusher paddle with your hand. Pass: `delivery_failed` with
   "pusher stalled". If it never trips, lower `SG_THRESHOLD`. If it trips with no load, raise it.
2. Same for the lift: put a 10 kg weight on the platform; it must not trip at 5 kg (a full stack and the
   carriage) but must trip when blocked.

### 4. Paper (needs the printer, or real stacks by hand)
1. Stacks of 10, 100, 300 and 500 sheets on the platform, delivery to slot 1, 20 times each.
   Pass: the stack arrives square, no sheets left on the platform, paper sensor sees it every time.
2. Watch for the top sheets sliding ahead of the paddle (shingling) on tall stacks. If you see it, add a
   light top guide or lower the pusher speed (`pushSpeed`).

### 5. Acceptance
1. With the rig wired for compartment 1 only, run the acceptance tests against the board:
   `cd kiosk-agent && PL_CONFORMANCE_PORT=/dev/ttyUSB0 pytest tests/test_controller_protocol.py`.
   The tests use slots 1–8, so for the 1-compartment rig temporarily set `N_COMPARTMENTS` to 8 and leave
   slots 2–8 without hardware; their sensors then read "door open", so run with doors 2–8 bridged closed.
2. Connect the Raspberry Pi, run the agent with `PL_HARDWARE_MODE=serial`, and send a real print from the
   app. Pass: the job goes printing → delivering → ready, and the QR opens the door.

Record the calibrated numbers and the results in this file.
