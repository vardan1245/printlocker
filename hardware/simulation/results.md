| Check | Value | Limit | Result | Note |
|---|---|---|---|---|
| 500-sheet stack height | 50 mm | opening 72 mm | PASS | 22 mm headroom |
| Pusher paddle vs ceiling | 55 mm paddle | opening 72 mm | PASS | 15 mm clearance |
| Pusher force needed (500 sheets, mu 0.5) | 12.2 N | motor gives 33 N | PASS | safety factor 2.7 |
| Lift torque per screw (5.0 kg moving) | 0.104 N·m | motor 0.21 N·m | PASS | safety factor 2.0 |
| Lift holds position without power | lead angle 17.7° | friction angle 8.5° | CHECK | T8x8 back-drives: keep drivers enabled (hold current) and park the lift at the bottom stop |
| Lift speed | 80 mm/s |  | PASS | 600 rpm x 8 mm lead |
| Delivery after last sheet (compartment 1 / 8) | 17 s / 33 s | < 60 s | PASS | avg 25 s; next job waits until the lift is back |
| 200-page duplex job, print time | 267 s |  | PASS | delivery adds 9% |
| 24 V peak load (all at once, incl. converter losses) | 80 W | PSU 150 W | PASS | 53% of PSU |

Delivery time by compartment (s): 1: 17, 2: 20, 3: 22, 4: 24, 5: 26, 6: 28, 7: 31, 8: 33

24 V loads: 3 steppers moving (TMC2209, ~10 W each) 30 W; Solenoid lock pulse (12 V 1 A, one at a time) 12 W; Raspberry Pi 5 + 7" screen 15 W; ESP32, expanders, sensors 3 W; Fans 120 + 80 mm 5 W; LED strip + door LEDs 5 W. The printer runs on its own 230 V socket.
