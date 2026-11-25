"""PrintLocker kiosk - first-pass motor, timing and power checks.

Plain Python, no dependencies:  python3 calc_mechanics.py > results.md
Every input is an assumption listed in ASSUME; change one and re-run. Numbers marked (datasheet) come
from typical catalogue values and must be checked against the parts actually bought.
"""
import math

G = 9.81
ASSUME = dict(
    sheet_g=0.210 * 0.297 * 80,       # A4 80 g/m2 -> grams per sheet
    sheet_mm=0.10,                    # thickness of one 80 g/m2 sheet
    max_sheets=500,                   # biggest stack one compartment takes
    mu_paper_steel=0.5,               # worst case; 0.2-0.4 typical, 0.5 covers humid paper
    carriage_kg=2.5,                  # platform + arms + blocks + nuts + pusher motor + guides (estimate)
    nema17_hold_nm=0.42,              # 17HS4401-class motor (datasheet)
    nema17_usable_frac=0.5,           # usable torque at working speed vs holding torque
    gt2_teeth=20,                     # pusher pulley
    lead_mm=8,                        # T8 lead screw, 4-start, 8 mm lead
    screw_d_mm=8,
    mu_brass_steel=0.15,
    screw_efficiency=0.30,            # brass nut on T8x8, dry/lightly greased
    lift_rpm=600,
    pusher_mm_s=100,
    accel_s=0.4,                      # ramp up/down time per move
    pitch_mm=90, first_floor_mm=516, receive_mm=346, n_comp=8, push_mm=320,
    settle_s=5,                       # "no new sheet for 5 s" = job finished
    ppm_simplex=45, ipm_duplex=22.5,  # Kyocera PA4500x (datasheet)
)
A = ASSUME


def move_time(dist_mm, speed_mm_s, ramp_s=A['accel_s']):
    """Trapezoid profile: ramps of ramp_s at each end."""
    cruise = dist_mm / speed_mm_s
    return cruise + ramp_s


def main():
    rows = []

    def row(check, value, limit, ok, note=''):
        rows.append((check, value, limit, 'PASS' if ok else 'CHECK', note))

    stack_kg = A['sheet_g'] * A['max_sheets'] / 1000
    stack_mm = A['sheet_mm'] * A['max_sheets']
    row('500-sheet stack height', '%.0f mm' % stack_mm, 'opening 72 mm', stack_mm < 72 - 10,
        '%.0f mm headroom' % (72 - stack_mm))
    row('Pusher paddle vs ceiling', '55 mm paddle', 'opening 72 mm', 55 < 72 - 5, '15 mm clearance')

    # --- pusher
    push_n = A['mu_paper_steel'] * stack_kg * G
    r_pulley = A['gt2_teeth'] * 2 / (2 * math.pi) / 1000
    pusher_n = A['nema17_hold_nm'] * A['nema17_usable_frac'] / r_pulley
    row('Pusher force needed (500 sheets, mu 0.5)', '%.1f N' % push_n, 'motor gives %.0f N' % pusher_n,
        pusher_n > 2 * push_n, 'safety factor %.1f' % (pusher_n / push_n))

    # --- lift
    load_n = (A['carriage_kg'] + stack_kg) * G
    per_screw = load_n / 2
    torque = per_screw * A['lead_mm'] / 1000 / (2 * math.pi * A['screw_efficiency'])
    usable = A['nema17_hold_nm'] * A['nema17_usable_frac']
    row('Lift torque per screw (%.1f kg moving)' % (load_n / G), '%.3f N·m' % torque, 'motor %.2f N·m' % usable,
        usable > 1.5 * torque, 'safety factor %.1f' % (usable / torque))
    lift_speed = A['lead_mm'] * A['lift_rpm'] / 60
    lead_angle = math.degrees(math.atan(A['lead_mm'] / (math.pi * A['screw_d_mm'])))
    friction_angle = math.degrees(math.atan(A['mu_brass_steel']))
    self_locking = lead_angle < friction_angle
    row('Lift holds position without power', 'lead angle %.1f°' % lead_angle, 'friction angle %.1f°' % friction_angle,
        self_locking, 'T8x8 back-drives: keep drivers enabled (hold current) and park the lift at the bottom stop'
        if not self_locking else 'self-locking')

    # --- timing
    travels = [A['first_floor_mm'] + i * A['pitch_mm'] - A['receive_mm'] for i in range(A['n_comp'])]
    up = [move_time(t, lift_speed) for t in travels]
    push = 2 * move_time(A['push_mm'], A['pusher_mm_s'])
    cycle = [A['settle_s'] + u + push + u for u in up]
    row('Lift speed', '%.0f mm/s' % lift_speed, '', True, '%d rpm x %d mm lead' % (A['lift_rpm'], A['lead_mm']))
    row('Delivery after last sheet (compartment 1 / 8)', '%.0f s / %.0f s' % (cycle[0], cycle[-1]), '< 60 s',
        max(cycle) < 60, 'avg %.0f s; next job waits until the lift is back' % (sum(cycle) / len(cycle)))
    t200 = 100 / A['ipm_duplex'] * 60
    row('200-page duplex job, print time', '%.0f s' % t200, '', True,
        'delivery adds %.0f%%' % (100 * (sum(cycle) / len(cycle)) / t200))

    # --- power (24 V side)
    loads = [('3 steppers moving (TMC2209, ~10 W each)', 30), ('Solenoid lock pulse (12 V 1 A, one at a time)', 12),
             ('Raspberry Pi 5 + 7" screen', 15), ('ESP32, expanders, sensors', 3), ('Fans 120 + 80 mm', 5),
             ('LED strip + door LEDs', 5)]
    peak = sum(w for _, w in loads) / 0.88          # buck converter losses
    row('24 V peak load (all at once, incl. converter losses)', '%.0f W' % peak, 'PSU 150 W', peak < 0.7 * 150,
        '%.0f%% of PSU' % (100 * peak / 150))

    print('| Check | Value | Limit | Result | Note |')
    print('|---|---|---|---|---|')
    for r in rows:
        print('| %s | %s | %s | %s | %s |' % r)
    print()
    print('Delivery time by compartment (s): ' + ', '.join('%d: %.0f' % (i + 1, c) for i, c in enumerate(cycle)))
    print()
    print('24 V loads: ' + '; '.join('%s %d W' % l for l in loads) + '. The printer runs on its own 230 V socket.')


if __name__ == '__main__':
    main()
