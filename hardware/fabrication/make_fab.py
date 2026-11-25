"""PrintLocker kiosk - fabrication files for the workshop, generated from the 3D model's parameters.

    python3 make_fab.py        # writes dxf/*.dxf, drawings.pdf, sheet_parts.csv, cut_list.csv, preview.png

Sizes come from ../fusion/build_kiosk.py (its P dict is read directly), so the model and these files
stay in step. Flat patterns use a bend deduction for 90° air bends with inner radius = thickness and
K = 0.33. Ask the shop for their own deduction and regenerate if it differs (BEND_K below).

DXF layers: CUT (outline + holes, cut through), BEND (bend lines; flange bent DOWN = away from viewer),
TEXT (part label, not cut). Units mm, R12 ASCII.
"""
from __future__ import annotations

import ast
import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).parent
MODEL = HERE.parent / "fusion" / "build_kiosk.py"
BEND_K = 0.33


def model_params() -> dict:
    tree = ast.parse(MODEL.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "P":
            return {kw.arg: ast.literal_eval(kw.value) for kw in node.value.keywords}
    raise SystemExit("P dict not found in build_kiosk.py")


def bend_deduction(t: float, r: float | None = None) -> float:
    r = t if r is None else r
    ba = math.pi / 2 * (r + BEND_K * t)
    return 2 * (r + t) - ba


# ------------------------------------------------------------------ geometry of a flat part
@dataclass
class Part:
    name: str
    material: str
    t: float
    qty: int
    w: float                                    # flat width (x)
    h: float                                    # flat height (y)
    notch: float = 0                            # square corner relief for 4-side flanged trays
    holes: list = field(default_factory=list)   # (x, y, diameter)
    cutouts: list = field(default_factory=list)  # (x, y, w, h) rectangles
    bends: list = field(default_factory=list)   # ((x1, y1), (x2, y2))
    note: str = ""
    poly: list = field(default_factory=list)    # explicit outline (overrides the rectangle)

    def outline(self) -> list[tuple[float, float]]:
        if self.poly:
            return self.poly
        w, h, n = self.w, self.h, self.notch
        if not n:
            return [(0, 0), (w, 0), (w, h), (0, h)]
        return [(n, 0), (w - n, 0), (w - n, n), (w, n), (w, h - n), (w - n, h - n), (w - n, h), (n, h),
                (n, h - n), (0, h - n), (0, n), (n, n)]


def holes_along(x0, y0, x1, y1, d=5.5, pitch=300, margin=40):
    """Evenly spaced holes on a line, at most `pitch` apart, starting `margin` from each end."""
    length = math.hypot(x1 - x0, y1 - y0)
    usable = max(0.0, length - 2 * margin)
    n = max(2, math.ceil(usable / pitch) + 1)
    out = []
    for i in range(n):
        f = (margin + usable * i / (n - 1)) / length if length else 0
        out.append((round(x0 + (x1 - x0) * f, 1), round(y0 + (y1 - y0) * f, 1), d))
    return out


def tray(name, w, h, t, qty, flange, material, note="", cutouts=(), holes=()):
    """Face w x h with a flange folded back on all four sides (doors and panels)."""
    bd = bend_deduction(t)
    ext = flange - bd / 2           # flat length each flange adds beyond the face's mould line
    W, H = w + 2 * ext, h + 2 * ext
    inset = flange - bd / 2
    p = Part(name, material, t, qty, round(W, 1), round(H, 1), notch=round(ext + t / 2, 1), note=note)  # relief just past the bend
    p.bends = [((inset, 0), (inset, H)), ((W - inset, 0), (W - inset, H)),
               ((0, inset), (W, inset)), ((0, H - inset), (W, H - inset))]
    p.cutouts = [(x + ext, y + ext, cw, ch) for x, y, cw, ch in cutouts]
    p.holes = [(x + ext, y + ext, d) for x, y, d in holes]
    return p


def shelf(name, w, depth, lip, side, t, qty, material, note=""):
    """Shelf: flat area w x depth, front lip folded down, side flanges folded down for rivets."""
    bd = bend_deduction(t)
    W = w + 2 * (side - bd / 2)
    H = depth + lip - bd / 2
    p = Part(name, material, t, qty, round(W, 1), round(H, 1), note=note)
    sx = side - bd / 2
    p.bends = [((sx, 0), (sx, H)), ((W - sx, 0), (W - sx, H)), ((sx, lip - bd / 2), (W - sx, lip - bd / 2))]
    ly = round(lip - bd / 2, 1)
    sx_ = round(sx, 1)
    p.poly = [(sx_, 0), (p.w - sx_, 0), (p.w - sx_, ly), (p.w, ly), (p.w, p.h), (0, p.h), (0, ly), (sx_, ly)]
    p.holes = holes_along(sx / 2, lip + 30, sx / 2, H - 20, d=4.2, pitch=150, margin=0) + \
        holes_along(W - sx / 2, lip + 30, W - sx / 2, H - 20, d=4.2, pitch=150, margin=0)
    return p


# ------------------------------------------------------------------ the parts
def parts(P) -> list[Part]:
    W, D, H, F, S = P["W"], P["D"], P["H"], P["F"], P["SKIN"]
    base, bay, n, pitch, shaft, duct = P["BASE"], P["BAY_TOP"], P["N"], P["PITCH"], P["SHAFT_Y"], P["DUCT_X"]
    xl = F + S                                  # inner face of the posts
    zt = H - S - F                              # top ring
    head_floor = bay + 18 + n * pitch
    skin_z0 = base - 30
    ring_z = [base - F, bay - F, head_floor, zt]
    out: list[Part] = []
    st15, st20, al = "Steel DC01, powder-coated", "Steel DX51D galvanised", "Aluminium 6082"

    # Side skins: screwed to posts (front y 15, rear y D-S-15) and to the rings + slide rail.
    sh = H - skin_z0
    holes = []
    for y in (F / 2, D - S - F / 2):
        holes += holes_along(y, 0, y, sh)
    for z in ring_z + [100]:
        holes += holes_along(F + 40, z + F / 2 - skin_z0, D - F - 40, z + F / 2 - skin_z0)
    out.append(Part("Side skin", st15, S, 2, D, sh, holes=holes, note="Left and right identical; flat"))

    top_w = W - 2 * S
    holes = holes_along(F / 2, F + 16, F / 2, D - F - 16) + holes_along(top_w - F / 2, F + 16, top_w - F / 2, D - F - 16)
    holes += holes_along(40, 16 + F / 2, top_w - 40, 16 + F / 2) + holes_along(40, D - S - F / 2, top_w - 40, D - S - F / 2)
    out.append(Part("Top skin", st15, S, 1, top_w, D, holes=holes, note="Flat"))

    back_h = H - base + 30 - S
    holes = holes_along(F / 2, 0, F / 2, back_h) + holes_along(top_w - F / 2, 0, top_w - F / 2, back_h)
    for z in ring_z:
        holes += holes_along(F + 40, z + F / 2 - skin_z0, top_w - F - 40, z + F / 2 - skin_z0)
    cut = [(220 - S, 160 - skin_z0, 120, 120), (240 - S, 1600 - skin_z0, 80, 80), (440 - S, 120 - skin_z0, 50, 32)]
    fanholes = []
    for cx, cy, s in ((280 - S, 220 - skin_z0, 105), (280 - S, 1640 - skin_z0, 71.5)):
        fanholes += [(cx + dx * s / 2, cy + dy * s / 2, 4.5) for dx in (-1, 1) for dy in (-1, 1)]
    out.append(Part("Back skin (service panel)", st15, S, 1, top_w, back_h, holes=holes + fanholes, cutouts=cut,
                    note="Removable; cut-outs for 120 mm fan, 80 mm fan and IEC inlet. Add a grille over the fan holes"))

    out.append(Part("Kick plate", st15, S, 1, W - 2 * xl, base - 30, note="Flat; screwed to the base ring"))
    out.append(Part("Bottom panel", st20, S, 1, W - 2 * xl, D - 2 * F - S, note="Flat; sits on the base ring"))

    full_w, part_w, depth = W - 2 * xl, duct - xl, shaft - F
    out.append(shelf("Shelf, full width (bay top, head floor)", full_w, depth, 16, 20, P["SHEET"], 2, st20,
                     "Front lip and side flanges bent down; blind-rivet the flanges to the side skins"))
    out.append(shelf("Shelf, compartment", part_w, depth, 16, 20, P["SHEET"], n - 1, st20,
                     "Left flange to the side skin, right flange to the cable-duct partition"))
    duct_h = (bay + 18 + (n - 1) * pitch + pitch - 20) - (bay + 18)
    out.append(Part("Cable duct partition", st20, P["SHEET"], 1, depth, duct_h,
                    holes=[(x, y, 4.2) for x in (20, depth - 20) for y in range(30, int(duct_h), 90)],
                    note="Flat; rivet holes for the compartment shelf flanges"))
    plate_h = zt - head_floor
    out.append(Part("Electronics plate", st20, P["SHEET"], 1, full_w, plate_h,
                    holes=[(x, y, 4.2) for x in (15, full_w - 15) for y in (20, plate_h / 2, plate_h - 20)],
                    note="Flat; components mounted with M3 standoffs drilled on assembly"))

    x0 = S + F + 1
    dw = W - 2 * x0
    door_h = pitch - 2
    out.append(tray("Compartment door", dw, door_h, S, n, 15, st15,
                    "Flanges folded back 90°. Finger groove; piano hinge on the left, strike riveted at the right",
                    cutouts=[(230 - x0, 7, 100, 8)]))
    bay_h = bay - base - 3
    out.append(tray("Printer bay door", dw, bay_h, S, 1, 15, st15,
                    "Cam-lock hole Ø19; piano hinge on the left", holes=[(dw - 31, 337, 19)]))
    head_z0 = head_floor + 1
    head_h = H - S - F - head_z0 - 1
    out.append(tray("Head panel (screen, scanner)", dw, head_h, S, 1, 15, st15,
                    "Screen opening 194 × 110, scanner window 70 × 45; piano hinge on the left",
                    cutouts=[(183 - x0, 1345 - head_z0, 194, 110), (245 - x0, 1270 - head_z0, 70, 45)]))
    out.append(Part("Printer shelf (drawer)", st20, 2, 1, round(W - 104 + 2 * (30 - bend_deduction(2) / 2), 1), 440,
                    bends=[((30 - bend_deduction(2) / 2, 0), (30 - bend_deduction(2) / 2, 440)),
                           ((W - 104 + 30 - bend_deduction(2) / 2, 0), (W - 104 + 30 - bend_deduction(2) / 2, 440))],
                    note="Side flanges bent UP 30 mm; drill to the drawer slides on assembly"))
    out.append(Part("Lift platform", al, 6, 1, 230, 324, note="Machined plate; slot for the pusher belt on assembly"))
    out.append(Part("Motor bracket plate", al, 6, 1, 440, 52,
                    holes=[(70, 26, 22), (370, 26, 22)] + [(70 + dx, 26 + dy, 3.4) for dx in (-15.5, 15.5) for dy in (-15.5, 15.5)]
                    + [(370 + dx, 26 + dy, 3.4) for dx in (-15.5, 15.5) for dy in (-15.5, 15.5)],
                    note="NEMA17 pattern 31 × 31, centre bore Ø22 for the coupler"))
    out.append(Part("Rear flap", "Polycarbonate", 3, n, 240, pitch - 18 - 10, note="One-way flap, hinged at its top edge"))
    out.append(Part("Platform guide", "Polycarbonate", 4, 2, 324, 60))
    return out


def cut_list(P) -> list[dict]:
    W, D, H, F, S = P["W"], P["D"], P["H"], P["F"], P["SKIN"]
    base, zt = P["BASE"], H - S - F
    post = zt - base + F + F                    # from base ring bottom to top of the top ring
    rail_x = W - 2 * S - 2 * F
    rail_y = D - S - F - F
    return [
        {"item": "Post", "profile": "30×30 B-type slot 8", "length_mm": post, "qty": 4, "ends": "M6 tapped both ends"},
        {"item": "Rail, front/rear (4 rings)", "profile": "30×30 B-type slot 8", "length_mm": rail_x, "qty": 8, "ends": "square"},
        {"item": "Rail, side (4 rings + 2 slide rails)", "profile": "30×30 B-type slot 8", "length_mm": rail_y, "qty": 10, "ends": "square"},
        {"item": "Lift guide", "profile": "20×40 B-type slot 6", "length_mm": 1258, "qty": 2, "ends": "M5 tapped both ends"},
        {"item": "Linear rail", "profile": "MGN12H, 1 block each", "length_mm": 1220, "qty": 2, "ends": "cut by supplier"},
        {"item": "Lead screw", "profile": "T8, 8 mm lead, brass nut", "length_mm": 1230, "qty": 2, "ends": "8 mm journal at the top"},
    ]


# ------------------------------------------------------------------ writers
def dxf(part: Part, path: Path) -> None:
    def ent(kind, layer, *kv):
        return [ "0", kind, "8", layer] + [str(x) for x in kv]

    lines = ["0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
    pts = part.outline()
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        lines += ent("LINE", "CUT", 10, x1, 20, y1, 11, x2, 21, y2)
    for x, y, w, h in part.cutouts:
        for (a, b), (c, d) in (((x, y), (x + w, y)), ((x + w, y), (x + w, y + h)), ((x + w, y + h), (x, y + h)), ((x, y + h), (x, y))):
            lines += ent("LINE", "CUT", 10, a, 20, b, 11, c, 21, d)
    for x, y, dia in part.holes:
        lines += ent("CIRCLE", "CUT", 10, x, 20, y, 40, dia / 2)
    for (x1, y1), (x2, y2) in part.bends:
        lines += ent("LINE", "BEND", 10, x1, 20, y1, 11, x2, 21, y2)
    lines += ent("TEXT", "TEXT", 10, 5, 20, part.h + 8, 40, 6, 1, f"{part.name} - {part.material} {part.t} mm - qty {part.qty}")
    lines += ["0", "ENDSEC", "0", "EOF"]
    path.write_text("\n".join(lines) + "\n")


def slug(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-").replace("--", "-")


def draw(ax, part: Part, dims=True):
    from matplotlib.patches import Circle, Polygon, Rectangle

    ax.add_patch(Polygon(part.outline(), closed=True, fill=True, fc="#eef2f7", ec="#1e293b", lw=1.2))
    for x, y, w, h in part.cutouts:
        ax.add_patch(Rectangle((x, y), w, h, fc="white", ec="#1e293b", lw=1))
    for x, y, d in part.holes:
        ax.add_patch(Circle((x, y), d / 2, fc="white", ec="#1e293b", lw=0.8))
    for (x1, y1), (x2, y2) in part.bends:
        ax.plot([x1, x2], [y1, y2], ls=(0, (6, 4)), color="#c2410c", lw=1)
    m = max(part.w, part.h) * 0.08
    ax.set_xlim(-m * 1.6, part.w + m)
    ax.set_ylim(-m * 1.6, part.h + m)
    ax.set_aspect("equal")
    ax.axis("off")
    if dims:
        ax.annotate("", (0, -m * 0.7), (part.w, -m * 0.7), arrowprops=dict(arrowstyle="<->", lw=0.8))
        ax.text(part.w / 2, -m * 1.25, f"{part.w:g}", ha="center", fontsize=9)
        ax.annotate("", (-m * 0.7, 0), (-m * 0.7, part.h), arrowprops=dict(arrowstyle="<->", lw=0.8))
        ax.text(-m * 1.25, part.h / 2, f"{part.h:g}", va="center", rotation=90, fontsize=9)


def main() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    P = model_params()
    ps = parts(P)
    (HERE / "dxf").mkdir(exist_ok=True)
    for p in ps:
        dxf(p, HERE / "dxf" / f"{slug(p.name)}.dxf")

    with open(HERE / "sheet_parts.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["part", "file", "material", "thickness_mm", "qty", "flat_w_mm", "flat_h_mm", "holes", "cutouts", "bends", "note"])
        for p in ps:
            wr.writerow([p.name, f"dxf/{slug(p.name)}.dxf", p.material, p.t, p.qty, p.w, p.h, len(p.holes),
                         len(p.cutouts), len(p.bends), p.note])
    cl = cut_list(P)
    with open(HERE / "cut_list.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(cl[0]))
        wr.writeheader()
        wr.writerows(cl)

    with PdfPages(HERE / "drawings.pdf") as pdf:
        fig = plt.figure(figsize=(11.69, 8.27))
        fig.text(0.05, 0.92, "PrintLocker kiosk - fabrication drawings", fontsize=18, weight="bold")
        fig.text(0.05, 0.88, f"1 printer, {P['N']} compartments, {P['W']} × {P['D']} × {P['H']} mm. Units mm. "
                 f"Bend deduction for 90° bends, r = t, K = {BEND_K}: 1.5 mm → {bend_deduction(1.5):.2f}, "
                 f"2 mm → {bend_deduction(2):.2f}. Dashed orange = bend line.", fontsize=9)
        rows = [["Part", "Material", "t", "Qty", "Flat size"]] + [[p.name, p.material, f"{p.t:g}", str(p.qty), f"{p.w:g} × {p.h:g}"] for p in ps]
        tbl = fig.add_axes([0.05, 0.38, 0.9, 0.47])
        tbl.axis("off")
        tbl.table(cellText=rows[1:], colLabels=rows[0], loc="upper left", cellLoc="left", colLoc="left",
                  colWidths=[0.42, 0.26, 0.06, 0.06, 0.2]).set_fontsize(8)
        rows2 = [["Profile item", "Profile", "Length", "Qty", "Ends"]] + [[c["item"], c["profile"], f"{c['length_mm']:g}", str(c["qty"]), c["ends"]] for c in cl]
        tbl2 = fig.add_axes([0.05, 0.05, 0.9, 0.28])
        tbl2.axis("off")
        tbl2.table(cellText=rows2[1:], colLabels=rows2[0], loc="upper left", cellLoc="left", colLoc="left",
                   colWidths=[0.32, 0.26, 0.1, 0.06, 0.26]).set_fontsize(8)
        pdf.savefig(fig)
        plt.close(fig)
        for p in ps:
            fig, ax = plt.subplots(figsize=(11.69, 8.27))
            fig.subplots_adjust(0.04, 0.12, 0.96, 0.9)
            draw(ax, p)
            fig.text(0.04, 0.94, p.name, fontsize=16, weight="bold")
            fig.text(0.04, 0.91, f"{p.material}, {p.t:g} mm  ·  qty {p.qty}  ·  flat {p.w:g} × {p.h:g} mm  ·  "
                     f"{len(p.holes)} holes, {len(p.cutouts)} cut-outs, {len(p.bends)} bends  ·  dxf/{slug(p.name)}.dxf",
                     fontsize=9, color="#334155")
            if p.note:
                fig.text(0.04, 0.05, p.note, fontsize=9, color="#334155")
            pdf.savefig(fig)
            plt.close(fig)

    cols = 4
    rows_n = math.ceil(len(ps) / cols)
    fig, axs = plt.subplots(rows_n, cols, figsize=(16, 4 * rows_n))
    for ax in axs.flat:
        ax.axis("off")
    for ax, p in zip(axs.flat, ps):
        draw(ax, p, dims=False)
        ax.set_title(f"{p.name}\n{p.t:g} mm · qty {p.qty} · {p.w:g} × {p.h:g}", fontsize=9)
    fig.tight_layout()
    fig.savefig(HERE / "preview.png", dpi=80)
    print(f"{len(ps)} sheet/plate parts, {sum(p.qty for p in ps)} pieces; profile cut list {len(cl)} lines; "
          f"30×30 total {sum(c['length_mm'] * c['qty'] for c in cl if c['profile'].startswith('30')) / 1000:.1f} m")


if __name__ == "__main__":
    main()
