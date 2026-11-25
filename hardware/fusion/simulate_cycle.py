"""PrintLocker kiosk - motion simulation of one full cycle in Autodesk Fusion.

Needs the model from build_kiosk.py (moving parts are top-level components named "MOVE ...").
For every frame it poses the moving parts, runs Fusion's interference analysis over all bodies,
saves an image to ~/PrintLocker/simulation/frames and appends the result to frames.jsonl.

Run it in chunks (Fusion scripts should stay short): set START / END, run, repeat.
START = 0 also clears the output folder. END = None runs to the end and restores the home pose.
"""
import json
import math
import os

import adsk.core
import adsk.fusion

START, END = 0, None
TARGET = 5                 # compartment the job goes to
OUT = os.path.expanduser('~/PrintLocker/simulation/frames')
IMG_W, IMG_H = 1280, 960
PULL_BACK = 1.12           # camera distance after fitting the home pose

BAY_TOP, PITCH, RECEIVE_TOP, SHAFT_Y, HINGE_X = 498, 90, 346, 500, 32.5
PUSH = 320                 # pusher stroke (mm)
DOOR_OPEN, FLAP_OPEN = 100, 70   # degrees
DRAWER_OUT = 380

# Contacts that are intended, not clashes: the lead screws run through the nuts and arms,
# and each nut sits inside its arm.
ALLOWED = [('Lead screw', 'Screw nut'), ('Lead screw', 'Lift arm'), ('Lift arm', 'Screw nut')]

app = adsk.core.Application.get()
design = adsk.fusion.Design.cast(app.activeProduct)
YUP = app.preferences.generalPreferences.defaultModelingOrientation == \
    adsk.core.DefaultModelingOrientations.YUpModelingOrientation


# ------------------------------------------------------------------ coordinates
def fpt(x, y, z):
    return adsk.core.Point3D.create(x / 10, z / 10, -y / 10) if YUP else adsk.core.Point3D.create(x / 10, y / 10, z / 10)


def fvec(x, y, z):
    return adsk.core.Vector3D.create(x / 10, z / 10, -y / 10) if YUP else adsk.core.Vector3D.create(x / 10, y / 10, z / 10)


def model_y(p):
    return -p.z * 10 if YUP else p.y * 10


def move(dx=0, dy=0, dz=0):
    m = adsk.core.Matrix3D.create()
    m.translation = fvec(dx, dy, dz)
    return m


def rotate(deg, axis, origin, probe):
    """Rotation by deg about axis through origin, in the direction that moves `probe` towards -y."""
    best = None
    for sign in (1, -1):
        m = adsk.core.Matrix3D.create()
        m.setToRotation(sign * math.radians(deg), fvec(*axis), fpt(*origin))
        p = fpt(*probe)
        p.transformBy(m)
        if best is None or model_y(p) < best[0]:
            best = (model_y(p), m)
    return best[1] if deg else adsk.core.Matrix3D.create()


# ------------------------------------------------------------------ timeline
def zf(n):
    return BAY_TOP + 18 + (n - 1) * PITCH


def timeline():
    target_top = zf(TARGET)
    p = dict(lift=RECEIVE_TOP, pad=0, s_dy=0, s_dz=0, s_on=True, flap=0, door=0, bay=0, drawer=0)
    out = []

    def add(label, **kw):
        p.update(kw)
        out.append((label, dict(p)))

    def ramp(label, steps, **ends):
        start = {k: p[k] for k in ends}
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)                       # smooth start and stop
            vals = {k: start[k] + (v - start[k]) * t for k, v in ends.items()}
            if 'lift' in vals and p['s_dz'] == p['lift'] - RECEIVE_TOP and p['s_dy'] == p['pad']:
                vals['s_dz'] = vals['lift'] - RECEIVE_TOP  # stack rides on the platform
            if 'pad' in vals and p['s_dy'] == p['pad'] and vals['pad'] < p['pad']:
                vals['s_dy'] = vals['pad']                   # paddle pushes the stack forward
            add(label, **vals)

    for _ in range(3):
        add('1 Printing: sheets land face-up on the lift platform')
    ramp('2 Lift rises to compartment %d' % TARGET, 10, lift=target_top)
    for i in range(1, 9):
        dy = -PUSH * i / 8
        add('3 Pusher slides the stack through the rear flap', pad=dy, s_dy=dy,
            flap=FLAP_OPEN if dy < -5 else 0)
    for i in range(1, 7):
        dy = -PUSH + PUSH * i / 6
        add('4 Pusher returns, flap falls shut', pad=dy, flap=FLAP_OPEN if 802 + dy < SHAFT_Y + 2 else 0)
    ramp('5 Lift returns to the printer', 8, lift=RECEIVE_TOP)
    ramp('6 User scans the QR: door %d unlocks and opens' % TARGET, 5, door=DOOR_OPEN)
    add('7 User takes the prints', s_on=False)
    ramp('8 Door closes (reed switch confirms)', 5, door=0)
    ramp('9 Service: printer bay door opens', 4, bay=DOOR_OPEN)
    ramp('10 Service: printer slides out on its drawer', 6, drawer=-DRAWER_OUT)
    ramp('11 Service: printer slides back', 6, drawer=0)
    ramp('12 Service: bay door closes', 4, bay=0)
    return out


# ------------------------------------------------------------------ posing
def occ(name):
    o = design.rootComponent.occurrences
    for i in range(o.count):
        if o.item(i).component.name == name:
            return o.item(i)
    raise RuntimeError('missing component ' + name)


def pose(p):
    lz = p['lift'] - RECEIVE_TOP
    occ('MOVE Lift carriage').transform = move(dz=lz)
    occ('MOVE Pusher paddle').transform = move(dy=p['pad'], dz=lz)
    st = occ('MOVE Paper stack')
    st.isLightBulbOn = p['s_on']
    st.transform = move(dy=p['s_dy'], dz=p['s_dz'])
    top = zf(TARGET) + PITCH - 18 - 8                      # top edge of the flap = hinge
    occ('MOVE Flap %d' % TARGET).transform = rotate(p['flap'], (1, 0, 0), (0, SHAFT_Y, top), (280, SHAFT_Y, zf(TARGET) + 2))
    mid = zf(TARGET) + 30
    occ('MOVE Door %d' % TARGET).transform = rotate(p['door'], (0, 0, 1), (HINGE_X, 0, 0), (527, 0, mid))
    occ('MOVE Bay door').transform = rotate(p['bay'], (0, 0, 1), (HINGE_X, 0, 0), (527, 0, 300))
    occ('MOVE Printer drawer').transform = move(dy=p['drawer'])


def clashes():
    ents = adsk.core.ObjectCollection.create()
    for o in design.rootComponent.allOccurrences:
        if not o.isLightBulbOn:
            continue
        for b in o.bRepBodies:
            ents.add(b)
    inp = design.createInterferenceInput(ents)
    inp.areCoincidentFacesIncluded = False
    found = set()
    for r in design.analyzeInterference(inp):
        a, b = sorted((r.entityOne.name, r.entityTwo.name))
        if any((x in a and y in b) or (x in b and y in a) for x, y in ALLOWED):
            continue
        found.add('%s x %s (%.1f cm3)' % (a, b, r.interferenceBody.volume))
    return sorted(found)


def camera():
    """Fixed right-front view: first fit the whole kiosk, then pull back a bit so the drawer fits too."""
    vp = app.activeViewport
    cam = vp.camera
    cam.isSmoothTransition = False
    cam.cameraType = adsk.core.CameraTypes.PerspectiveCameraType
    cam.target = fpt(250, 430, 860)
    cam.eye = fpt(250 + 2300, 430 - 2100, 860 + 900)
    cam.upVector = fvec(0, 0, 1)
    cam.isFitView = True
    vp.camera = cam
    vp.fit()
    cam = vp.camera
    t, e = cam.target, cam.eye
    cam.eye = adsk.core.Point3D.create(t.x + (e.x - t.x) * PULL_BACK, t.y + (e.y - t.y) * PULL_BACK,
                                       t.z + (e.z - t.z) * PULL_BACK)
    cam.isFitView = False
    vp.camera = cam


def cutaway(on):
    hide = ('Skin side right', 'Skin top', 'Cable duct partition')
    for comp in design.allComponents:
        for b in comp.bRepBodies:
            if b.name.startswith(hide):
                b.isLightBulbOn = not on


def run(_context: str):
    frames = timeline()
    end = len(frames) if END is None else min(END, len(frames))
    os.makedirs(OUT, exist_ok=True)
    log = os.path.join(OUT, 'frames.jsonl')
    if START == 0:
        for f in os.listdir(OUT):
            os.remove(os.path.join(OUT, f))
    cutaway(True)
    pose(frames[0][1])
    camera()
    for i in range(START, end):
        label, p = frames[i]
        pose(p)
        adsk.doEvents()
        found = clashes()
        app.activeViewport.saveAsImageFile(os.path.join(OUT, 'f%03d.png' % i), IMG_W, IMG_H)
        with open(log, 'a') as fh:
            fh.write(json.dumps({'i': i, 'label': label, 'pose': p, 'clashes': found}) + '\n')
    if end == len(frames):
        pose(frames[0][1] | {'s_on': True})
        cutaway(False)
    # Results go to frames.jsonl; nothing is raised, so Fusion keeps the final pose.
