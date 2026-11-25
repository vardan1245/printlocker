"""Pose the kiosk at one frame of the last simulate_cycle.py run (reads frames.jsonl), in the cut-away view.
Handy for looking at a single step in Fusion. Set FR and run; FR = -1 restores the home pose."""
import json
import math
import os

import adsk.core
import adsk.fusion

FR = 0
TARGET, BAY_TOP, PITCH, RECEIVE_TOP, SHAFT_Y, HINGE_X, PULL_BACK = 5, 498, 90, 346, 500, 32.5, 1.12
LOG = os.path.expanduser('~/PrintLocker/simulation/frames/frames.jsonl')
app = adsk.core.Application.get()
design = adsk.fusion.Design.cast(app.activeProduct)
YUP = app.preferences.generalPreferences.defaultModelingOrientation == adsk.core.DefaultModelingOrientations.YUpModelingOrientation


def fpt(x, y, z):
    return adsk.core.Point3D.create(x / 10, z / 10, -y / 10) if YUP else adsk.core.Point3D.create(x / 10, y / 10, z / 10)


def fvec(x, y, z):
    return adsk.core.Vector3D.create(x / 10, z / 10, -y / 10) if YUP else adsk.core.Vector3D.create(x / 10, y / 10, z / 10)


def move(dx=0, dy=0, dz=0):
    m = adsk.core.Matrix3D.create()
    m.translation = fvec(dx, dy, dz)
    return m


def rotate(deg, axis, origin, probe):
    best = None
    for sign in (1, -1):
        m = adsk.core.Matrix3D.create()
        m.setToRotation(sign * math.radians(deg), fvec(*axis), fpt(*origin))
        p = fpt(*probe)
        p.transformBy(m)
        y = -p.z * 10 if YUP else p.y * 10
        if best is None or y < best[0]:
            best = (y, m)
    return best[1] if deg else adsk.core.Matrix3D.create()


def zf(n):
    return BAY_TOP + 18 + (n - 1) * PITCH


def occ(name):
    o = design.rootComponent.occurrences
    for i in range(o.count):
        if o.item(i).component.name == name:
            return o.item(i)


def pose(p):
    lz = p['lift'] - RECEIVE_TOP
    occ('MOVE Lift carriage').transform = move(dz=lz)
    occ('MOVE Pusher paddle').transform = move(dy=p['pad'], dz=lz)
    st = occ('MOVE Paper stack')
    st.isLightBulbOn = p['s_on']
    st.transform = move(dy=p['s_dy'], dz=p['s_dz'])
    occ('MOVE Flap %d' % TARGET).transform = rotate(p['flap'], (1, 0, 0), (0, SHAFT_Y, zf(TARGET) + PITCH - 26), (280, SHAFT_Y, zf(TARGET) + 2))
    occ('MOVE Door %d' % TARGET).transform = rotate(p['door'], (0, 0, 1), (HINGE_X, 0, 0), (527, 0, zf(TARGET) + 30))
    occ('MOVE Bay door').transform = rotate(p['bay'], (0, 0, 1), (HINGE_X, 0, 0), (527, 0, 300))
    occ('MOVE Printer drawer').transform = move(dy=p['drawer'])


def run(_context: str):
    home = dict(lift=RECEIVE_TOP, pad=0, s_dy=0, s_dz=0, s_on=True, flap=0, door=0, bay=0, drawer=0)
    on = FR >= 0
    for comp in design.allComponents:
        for b in comp.bRepBodies:
            if b.name.startswith(('Skin side right', 'Skin top', 'Cable duct partition')):
                b.isLightBulbOn = not on
    pose(home)
    vp = app.activeViewport
    cam = vp.camera
    cam.isSmoothTransition = False
    cam.cameraType = adsk.core.CameraTypes.PerspectiveCameraType
    cam.target = fpt(250, 430, 860)
    cam.eye = fpt(2550, -1670, 1760)
    cam.upVector = fvec(0, 0, 1)
    cam.isFitView = True
    vp.camera = cam
    vp.fit()
    cam = vp.camera
    t, e = cam.target, cam.eye
    cam.eye = adsk.core.Point3D.create(t.x + (e.x - t.x) * PULL_BACK, t.y + (e.y - t.y) * PULL_BACK, t.z + (e.z - t.z) * PULL_BACK)
    cam.isFitView = False
    vp.camera = cam
    if on:
        rows = [json.loads(line) for line in open(LOG)]
        pose([r for r in rows if r['i'] == FR][-1]['pose'])
