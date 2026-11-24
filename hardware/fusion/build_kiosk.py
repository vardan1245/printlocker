"""PrintLocker kiosk - concept model for Autodesk Fusion (metal construction).

Run in Fusion (Utilities > Scripts and Add-ins > + > this file, or paste into the MCP script runner).
It clears the active design and rebuilds the whole kiosk from the parameters below.

Model coordinates (mm): x = width (left to right), y = depth (front face y=0, towards the back),
z = height from the floor. Converted to Fusion's Y-up or Z-up automatically.

Construction: 30x30 aluminium extrusion frame, 1.5 mm powder-coated steel skins, folded sheet
shelves and doors. One column = one printer + six compartments; columns bolt side by side.

Layout of one column, bottom to top:
  0-80      levelling feet + base frame
  80-498    printer bay: Kyocera PA4500x on a pull-out shelf, PT-320 face-up output at the rear
  498-1116  six compartments (82 mm opening, takes 500+ sheets), doors with solenoid locks
  1116-1750 head: touch screen + QR scanner (column A) / blank panel (column B), electronics plate
  rear shaft (y 500-858): paper lift on two lead screws; a pusher shoves the stack into a compartment
"""
import adsk.core
import adsk.fusion

P = dict(
    W=560, D=860, H=1750,
    F=30,              # extrusion size (30x30)
    SKIN=1.5,          # steel skin thickness
    SHEET=2,           # shelf / plate thickness
    BASE=80,           # top of base frame (feet below)
    BAY_TOP=498,       # first shelf; shelf i top = BAY_TOP + 18 + i * PITCH
    N=6,               # compartments per column
    PITCH=100,         # shelf pitch -> 82 mm opening, takes a 500-sheet stack
    SHAFT_Y=500,       # compartments end / lift shaft starts
    DUCT_X=486,        # cable duct partition; locks live in the duct (488..528)
    COLUMNS=2,         # 2 columns = 2 printers, 12 compartments
    LIFT_AT=3,         # show the lift level with compartment index (0..5), -1 = at the printer
    RECEIVE_TOP=346,   # platform top when catching paper from the printer
)

MM = 0.1  # Fusion API works in cm
app = adsk.core.Application.get()
design = adsk.fusion.Design.cast(app.activeProduct)
tbm = adsk.fusion.TemporaryBRepManager.get()
_YUP = True
_LOOKS = {}
FRAME, SKIN, DOOR, SHELF = 'Aluminum - Satin', 'Paint - Enamel Glossy (Dark Grey)', 'Paint - Enamel Glossy (White)', 'Steel - Galvanized (Small)'


def look(name):
    if name not in _LOOKS:
        a = design.appearances.itemByName(name)
        if a is None:
            lib = app.materialLibraries.itemByName('Fusion Appearance Library')
            a = design.appearances.addByCopy(lib.appearances.itemByName(name), name)
        _LOOKS[name] = a
    return _LOOKS[name]


def pt(x, y, z):
    return adsk.core.Point3D.create(x * MM, z * MM, -y * MM) if _YUP else adsk.core.Point3D.create(x * MM, y * MM, z * MM)


def tbox(x, y, z, dx, dy, dz):
    a, b = pt(x, y, z), pt(x + dx, y + dy, z + dz)
    c = adsk.core.Point3D.create((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2)
    obb = adsk.core.OrientedBoundingBox3D.create(
        c, adsk.core.Vector3D.create(1, 0, 0), adsk.core.Vector3D.create(0, 1, 0),
        abs(b.x - a.x), abs(b.y - a.y), abs(b.z - a.z))
    return tbm.createBox(obb)


def add(comp, name, body, appearance):
    b = comp.bRepBodies.add(body)
    b.name = name
    b.appearance = look(appearance)
    return b


def box(comp, name, x, y, z, dx, dy, dz, appearance=SKIN):
    return add(comp, name, tbox(x, y, z, dx, dy, dz), appearance)


def cut(body, *tools):
    for t in tools:
        tbm.booleanOperation(body, t, adsk.fusion.BooleanTypes.DifferenceBooleanType)
    return body


def rod(comp, name, x, y, z0, z1, r, appearance):
    c = tbm.createCylinderOrCone(pt(x, y, z0), r * MM, pt(x, y, z1), r * MM)
    return add(comp, name, c, appearance)


def sub(parent, name):
    occ = parent.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    occ.component.name = name
    return occ.component


# ------------------------------------------------------------------ parts
def frame(c):
    """30x30 extrusion skeleton: 4 posts, rings at base / bay top / head floor / top, depth rails.
    Front rails sit 16 mm back so the doors close in front of them; the head-floor ring sits on top
    of the head floor so it never covers compartment 6."""
    W, D, H, F, S = P['W'], P['D'], P['H'], P['F'], P['SKIN']
    xl, xr = S, W - S - F                     # post x positions
    yf, yb = 0, D - S - F                     # front / back post y
    zt = H - S - F                            # top ring z
    for nx, x in (('L', xl), ('R', xr)):
        for ny, y in (('front', yf), ('rear', yb)):
            box(c, 'Post %s %s' % (ny, nx), x, y, P['BASE'] - F, F, F, zt - P['BASE'] + F, FRAME)
    head_floor = P['BAY_TOP'] + 18 + P['N'] * P['PITCH']
    rings = (('base', P['BASE'] - F), ('bay top', P['BAY_TOP'] - F), ('head floor', head_floor), ('top', zt))
    for name, z in rings:
        box(c, 'Rail %s front' % name, xl + F, 16, z, xr - xl - F, F, F, FRAME)
        box(c, 'Rail %s rear' % name, xl + F, yb, z, xr - xl - F, F, F, FRAME)
        for nx, x in (('L', xl), ('R', xr)):
            box(c, 'Rail %s side %s' % (name, nx), x, yf + F, z, F, yb - yf - F, F, FRAME)
    # depth rails that carry the printer slides
    for nx, x in (('L', xl), ('R', xr)):
        box(c, 'Slide rail %s' % nx, x, yf + F, 100, F, yb - yf - F, F, FRAME)
    for i, (x, y) in enumerate(((60, 60), (W - 60, 60), (60, D - 60), (W - 60, D - 60))):
        rod(c, 'Levelling foot %d' % (i + 1), x, y, 0, P['BASE'] - F, 12, 'Rubber - Hard')


def skins(c):
    W, D, H, S = P['W'], P['D'], P['H'], P['SKIN']
    box(c, 'Skin side left (1.5 mm steel)', 0, 0, P['BASE'] - 30, S, D, H - P['BASE'] + 30)
    box(c, 'Skin side right (1.5 mm steel)', W - S, 0, P['BASE'] - 30, S, D, H - P['BASE'] + 30)
    box(c, 'Skin top', S, 0, H - S, W - 2 * S, D, S)
    back = tbox(S, D - S, P['BASE'] - 30, W - 2 * S, S, H - P['BASE'] + 30 - S)
    cut(back, tbox(220, D - S - 1, 160, 120, S + 2, 120), tbox(240, D - S - 1, 1600, 80, S + 2, 80))
    add(c, 'Skin back (removable, shaft service)', back, SKIN)
    box(c, 'Skin bottom', P['F'] + S, P['F'], P['BASE'], W - 2 * (P['F'] + S), D - 2 * P['F'] - S, S, SHELF)
    box(c, 'Kick plate', P['F'] + S, 30, 0, W - 2 * (P['F'] + S), S, P['BASE'] - 30, SKIN)
    # folded sheet shelves; bay top and head floor span the full width, the others stop at the duct
    xl, y0, y1 = P['F'] + S, P['F'], P['SHAFT_Y']
    for i in range(P['N'] + 1):
        top = P['BAY_TOP'] + 18 + i * P['PITCH']
        full = i in (0, P['N'])
        w = (W - 2 * xl) if full else (P['DUCT_X'] - xl)
        name = 'Bay top shelf' if i == 0 else ('Head floor' if full else 'Shelf %d' % i)
        box(c, name, xl, y0, top - P['SHEET'], w, y1 - y0, P['SHEET'], SHELF)
        box(c, name + ' front lip', xl, y0, top - 18, w, P['SHEET'], 16, SHELF)
    zb, zt = P['BAY_TOP'] + 18, P['BAY_TOP'] + 18 + (P['N'] - 1) * P['PITCH'] + P['PITCH'] - 20
    box(c, 'Cable duct partition', P['DUCT_X'], y0, zb, P['SHEET'], y1 - y0, zt - zb, SHELF)
    rod(c, 'Cable bundle (locks, sensors)', 508, 440, zb, zt, 7, 'Plastic - Glossy (Black)')


def door_span():
    x0 = P['SKIN'] + P['F'] + 1
    return x0, P['W'] - 2 * x0


def printer_bay(c):
    x0, w = door_span()
    box(c, 'Bay door (folded steel, key lock)', x0, 0, P['BASE'] + 2, w, 15, P['BAY_TOP'] - P['BASE'] - 3, DOOR)
    box(c, 'Bay door cam lock', x0 + w - 40, -3, 410, 18, 3, 18, 'Chrome')
    box(c, 'Drawer slide L', 32, 40, 104, 13, 440, 30, 'Steel - Satin')
    box(c, 'Drawer slide R', P['W'] - 45, 40, 104, 13, 440, 30, 'Steel - Satin')
    box(c, 'Printer shelf (pull-out, 2 mm steel)', 46, 40, 110, P['W'] - 92, 440, 12, SHELF)
    box(c, 'Power strip (printer + PSU)', 60, 480, 82, 280, 45, 40, 'Plastic - Matte (Black)')
    box(c, 'Bay fan 120 mm', 220, P['D'] - P['SKIN'] - 25, 160, 120, 25, 120, 'Plastic - Matte (Black)')
    box(c, 'IEC inlet + fuse + switch', 440, P['D'], 120, 50, 8, 32, 'Plastic - Matte (Black)')


def printer(c):
    z0 = 122
    box(c, 'Kyocera PA4500x body', 90, 40, z0, 380, 410, 285, 'Plastic - Matte (White)')
    box(c, 'Paper cassette front', 100, 38, z0 + 10, 360, 2, 75, 'Plastic - Matte (Gray)')
    box(c, 'Operation panel', 380, 44, z0 + 285, 80, 40, 8, 'Plastic - Glossy (Black)')
    box(c, 'Top output tray (unused)', 140, 150, z0 + 285, 280, 200, 1, 'Plastic - Matte (Gray)')
    box(c, 'PT-320 face-up rear unit', 165, 450, z0 + 134, 230, 48, 130, 'Plastic - Matte (Gray)')
    box(c, 'PT-320 exit slot', 175, 498, z0 + 244, 210, 1, 6, 'Plastic - Glossy (Black)')


def lockers(c):
    x0, w = door_span()
    for i in range(P['N']):
        zf = P['BAY_TOP'] + 18 + i * P['PITCH']       # compartment floor (shelf top)
        zc = zf + P['PITCH'] - 18                     # ceiling (lip of the next shelf)
        mid = (zf + zc) / 2
        n = i + 1
        door = tbox(x0, 0, zf - 17, w, 15, P['PITCH'] - 2)
        cut(door, tbox(230, -1, zf - 10, 100, 5, 8))   # finger pull groove
        add(c, 'Door %d (folded steel)' % n, door, DOOR)
        box(c, 'Door %d number plate' % n, x0 + 10, -1, mid - 14, 34, 1, 28, 'Plastic - Matte (Black)')
        box(c, 'Door %d status LED' % n, x0 + w - 40, -1, mid - 4, 25, 1, 8, 'LED (Green)')
        box(c, 'Door %d strike' % n, 499, 15, mid - 8, 22, 3, 16, 'Steel - Satin')
        box(c, 'Lock %d (12 V solenoid)' % n, 496, 18, mid - 20, 28, 54, 41, 'Steel - Galvanized (Small)')
        box(c, 'Door %d magnet' % n, 40, 15, mid - 5, 10, 2, 10, 'Paint - Metallic (Silver)')
        box(c, 'Door %d reed switch' % n, 40, 31, mid - 5, 10, 10, 10, 'Plastic - Matte (Black)')
        box(c, 'Comp %d paper sensor (IR)' % n, 245, 300, zc - 8, 30, 15, 6, 'FR4')
        box(c, 'Comp %d rear flap (one-way)' % n, 160, P['SHAFT_Y'] - 3, zf + 2, 240, 3, zc - zf - 10,
            'Plastic - Translucent Matte (Gray)')
    zf = P['BAY_TOP'] + 18 + 1 * P['PITCH']
    box(c, 'Paper stack (job in comp 2)', 175, 185, zf, 210, 297, 30, 'Paper (White)')


def lift(c):
    top = P['RECEIVE_TOP'] if P['LIFT_AT'] < 0 else P['BAY_TOP'] + 18 + P['LIFT_AT'] * P['PITCH']
    for side, xe, xr, xb in (('L', 60, 80, 88), ('R', 480, 472, 459)):
        box(c, 'Lift extrusion 2040 %s' % side, xe, 788, P['BASE'] + 2, 20, 40, 1258, FRAME)
        box(c, 'MGN12 rail %s' % side, xr, 800, 110, 8, 12, 1220, 'Steel - Satin')
        box(c, 'MGN12 block %s' % side, xb, 796, top - 40, 13, 20, 40, 'Steel - Satin')
    for side, x in (('L', 130), ('R', 430)):
        rod(c, 'Lead screw T8 %s' % side, x, 803, 110, 1340, 4, 'Stainless Steel - Polished')
        box(c, 'Lift motor NEMA17 %s' % side, x - 21, 782, 1346, 42, 42, 48, 'Paint - Metallic (Black)')
        box(c, 'Screw nut %s' % side, x - 11, 792, top - 30, 22, 22, 12, 'Brass - Polished')
    box(c, 'Motor bracket plate', 60, 778, 1340, 440, 52, 6, FRAME)
    box(c, 'Lift arm L', 101, 778, top - 30, 64, 48, 24, FRAME)
    box(c, 'Lift arm R', 395, 778, top - 30, 64, 48, 24, FRAME)
    box(c, 'Platform', 165, 502, top - 6, 230, 324, 6, FRAME)
    box(c, 'Platform guide L', 161, 502, top, 4, 324, 60, 'Polycarbonate (Clear)')
    box(c, 'Platform guide R', 395, 502, top, 4, 324, 60, 'Polycarbonate (Clear)')
    box(c, 'Pusher paddle', 172, 802, top + 2, 216, 6, 55, 'Plastic - Matte (Red)')
    box(c, 'Pusher belt GT2', 276, 510, top - 14, 8, 300, 6, 'Rubber - Hard')
    box(c, 'Pusher motor NEMA17', 290, 690, top - 46, 42, 42, 40, 'Paint - Metallic (Black)')
    box(c, 'Paper stack on platform', 175, 505, top, 210, 297, 15, 'Paper (White)')
    box(c, 'Drag chain', 495, 700, 120, 30, 30, 1220, 'Plastic - Matte (Black)')
    box(c, 'Limit switch bottom', 60, 768, 120, 20, 20, 12, 'Plastic - Matte (Red)')
    box(c, 'Limit switch top', 60, 768, 1320, 20, 20, 12, 'Plastic - Matte (Red)')


def controller(c):
    """Electronics every column has, on the steel plate behind the head panel."""
    y = P['SHAFT_Y'] - 2
    xl = P['SKIN'] + P['F']
    top = P['H'] - P['SKIN'] - P['F']
    floor = P['BAY_TOP'] + 18 + P['N'] * P['PITCH']
    box(c, 'Electronics plate (2 mm steel)', xl, y, floor, P['W'] - 2 * xl, 2, top - floor, SHELF)
    box(c, 'PSU 24 V 150 W (LRS-150-24)', 40, y - 30, 1150, 159, 30, 97, 'Steel - Galvanized (Small)')
    box(c, 'Buck 24->12 V (locks)', 220, y - 22, 1150, 60, 22, 40, 'FR4')
    box(c, 'Column controller (ESP32 + MCP23017 + 3x TMC2209)', 200, y - 25, 1460, 150, 25, 100, 'FR4')
    box(c, 'MOSFET board 8 ch (locks)', 370, y - 20, 1460, 140, 20, 55, 'FR4')
    box(c, 'Terminal blocks on DIN rail', 40, y - 35, 1620, 460, 35, 45, 'Plastic - Matte (Gray)')
    box(c, 'Head fan 80 mm', 240, P['D'] - P['SKIN'] - 25, 1600, 80, 25, 80, 'Plastic - Matte (Black)')


def head_panel(c, name, holes):
    x0, w = door_span()
    z0 = P['BAY_TOP'] + 18 + P['N'] * P['PITCH'] + 1
    panel = tbox(x0, 0, z0, w, 15, P['H'] - P['SKIN'] - P['F'] - z0 - 1)
    cut(panel, *[tbox(*h) for h in holes])
    add(c, name, panel, DOOR)


def head_a(c):
    head_panel(c, 'Head panel (hinged service door)', [(183, -1, 1305, 194, 17, 110), (245, -1, 1200, 70, 17, 45)])
    box(c, 'Touch screen 7in (800x480)', 183, 0, 1305, 194, 22, 110, 'Plastic - Glossy (Black)')
    box(c, 'Scanner window', 245, 4, 1200, 70, 2, 45, 'Glass (Clear)')
    box(c, 'QR scanner module', 250, 15, 1202, 60, 40, 41, 'Plastic - Matte (Black)')
    box(c, 'Speaker', 410, 15, 1340, 40, 25, 40, 'Plastic - Matte (Black)')
    box(c, 'Status LED strip', 40, -2, 1690, 480, 2, 10, 'LED (Green)')
    box(c, 'Logo plate', 180, -2, 1550, 200, 2, 60, 'Paint - Enamel Glossy (Blue)')
    y = P['SHAFT_Y'] - 2
    box(c, 'Raspberry Pi 5 in case', 40, y - 32, 1300, 95, 32, 65, 'Plastic - Matte (Red)')
    box(c, 'Buck 24->5 V 5 A (Pi)', 300, y - 22, 1150, 60, 22, 40, 'FR4')
    box(c, 'Ethernet switch 5-port', 390, y - 26, 1150, 100, 26, 70, 'Plastic - Matte (Black)')


def head_b(c):
    head_panel(c, 'Head panel (hinged service door)', [])
    box(c, 'Status LED strip', 40, -2, 1690, 480, 2, 10, 'LED (Green)')
    box(c, 'Column label plate', 240, -2, 1550, 80, 2, 60, 'Paint - Enamel Glossy (Blue)')


def run(_context: str):
    global _YUP
    prefs = app.preferences.generalPreferences
    _YUP = prefs.defaultModelingOrientation == adsk.core.DefaultModelingOrientations.YUpModelingOrientation
    design.designType = adsk.fusion.DesignTypes.DirectDesignType
    root = design.rootComponent
    while root.occurrences.count:
        root.occurrences.item(0).deleteMe()
    while root.bRepBodies.count:
        root.bRepBodies.item(0).deleteMe()

    col = root.occurrences.addNewComponent(adsk.core.Matrix3D.create()).component
    col.name = 'Column (1 printer, %d compartments)' % P['N']
    frame(sub(col, 'Frame (30x30 extrusion)'))
    skins(sub(col, 'Skins and shelves (steel)'))
    printer_bay(sub(col, 'Printer bay'))
    printer(sub(col, 'Printer Kyocera PA4500x + PT-320'))
    lockers(sub(col, 'Compartments, doors, locks'))
    lift(sub(col, 'Paper lift + pusher'))
    controller(sub(col, 'Column electronics'))

    head_a(sub(root, 'Head A (screen, scanner, Pi)'))
    for k in range(1, P['COLUMNS']):
        m = adsk.core.Matrix3D.create()
        m.translation = adsk.core.Vector3D.create(k * P['W'] * MM, 0, 0)
        root.occurrences.addExistingComponent(col, m)
        hb = root.occurrences.addNewComponent(m)
        hb.component.name = 'Head %s (blank)' % chr(65 + k)
        head_b(hb.component)
    app.activeViewport.fit()
