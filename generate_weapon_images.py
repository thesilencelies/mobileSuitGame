#!/usr/bin/env python3
"""
generate_weapon_images.py

For each row in Weapon actions.csv, generates a 640×890 RGBA weapon
illustration in pictures/weapon_pictures/.

The base weapon sprite (pictures/weapons/<group>.png) is scaled, then
rotated so its topmost non-transparent pixel (the tip) points toward
the primary attack zone.

Motion lines are drawn BEHIND the weapon.  All arcs originate from the
weapon's butt/handle end (10 % of weapon length extended past that end)
so they look like the weapon is swinging from a pivot at its body:
  - High/Low (melee): outward-bowing quadratic Bézier, curving away from
    the weapon toward the right side of the card.
  - Mid (melee): tip-through quadratic Bézier; the arc passes through a
    point 10 % of weapon-length back from the tip along the shaft.
  - Ranged: dashed straight rays fanning from the weapon centre.
"""

import csv
import math
import os

import numpy as np
from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Canvas dimensions
# ---------------------------------------------------------------------------
CARD_W = 640
CARD_H = 890

# ---------------------------------------------------------------------------
# TikZ → pixel mapping
# Cards are rendered at scale=0.86 with ~100 px/cm.
# Attack boxes sit at TikZ x=6.2, y∈{7.5, 5.0, 2.5}.
# TikZ y=0 is the bottom of the canvas (y = CARD_H in pixels).
# ---------------------------------------------------------------------------
TIKZ_SCALE = 0.86
PX_PER_CM  = 100


def tikz_to_px(tx: float, ty: float) -> tuple:
    """TikZ centimetres → canvas pixel.  TikZ y-up ↔ image y-down."""
    return (
        round(tx * TIKZ_SCALE * PX_PER_CM),
        CARD_H - round(ty * TIKZ_SCALE * PX_PER_CM),
    )


# Attack zone centres (right side of card)
ZONE_CENTER = {
    "high": tikz_to_px(6.2, 7.5),   # ≈ (533, 245)
    "mid":  tikz_to_px(6.2, 5.0),   # ≈ (533, 460)
    "low":  tikz_to_px(6.2, 2.5),   # ≈ (533, 675)
}

# Motion-line accent colours (R, G, B, A)
ZONE_COLOR = {
    "high": (255, 215,  60, 210),
    "mid":  (240,  90,  90, 210),
    "low":  ( 70, 150, 255, 210),
}

# ---------------------------------------------------------------------------
# Weapon placement
# ---------------------------------------------------------------------------
WEAPON_CENTER = (220, 445)
WEAPON_MAX_H  = 600
WEAPON_MAX_W  = 340
# Short weapons are shifted right until their tip reaches this x,
# keeping the attacking end at a consistent distance from the zones.
TARGET_TIP_X  = 490

WEAPONS_DIR = "pictures/weapons"


# ---------------------------------------------------------------------------
# Sprite helpers
# ---------------------------------------------------------------------------

def find_weapon_image(group: str) -> str | None:
    key = group.lower().replace(" ", "")
    for fname in os.listdir(WEAPONS_DIR):
        if os.path.splitext(fname)[0].lower().replace(" ", "") == key:
            return os.path.join(WEAPONS_DIR, fname)
    return None


def find_tip(img: Image.Image) -> tuple:
    """Topmost non-transparent pixel — weapon tip."""
    arr   = np.array(img.convert("RGBA"))
    alpha = arr[:, :, 3]
    for y in range(arr.shape[0]):
        xs = np.where(alpha[y] > 10)[0]
        if len(xs):
            return int(xs.mean()), y
    return img.width // 2, 0


def find_bottom(img: Image.Image) -> tuple:
    """Bottommost non-transparent pixel — weapon butt/handle end."""
    arr   = np.array(img.convert("RGBA"))
    alpha = arr[:, :, 3]
    for y in range(arr.shape[0] - 1, -1, -1):
        xs = np.where(alpha[y] > 10)[0]
        if len(xs):
            return int(xs.mean()), y
    return img.width // 2, img.height - 1


# ---------------------------------------------------------------------------
# Rotation helpers
# ---------------------------------------------------------------------------

def pil_rotation_deg(tip: tuple, img_center: tuple,
                     target_canvas: tuple, weapon_canvas: tuple) -> float:
    """
    Rotation angle (degrees) for PIL.rotate() so the vector
    img_center→tip aligns with weapon_canvas→target_canvas.

    PIL.rotate(θ) applies the standard rotation matrix
      x' = x cosθ − y sinθ
      y' = x sinθ + y cosθ
    so θ = atan2(tgt_dy, tgt_dx) − atan2(tip_dy, tip_dx).
    Visually on screen (y-down) this is a clockwise rotation for +θ.
    """
    tip_dx = tip[0] - img_center[0]
    tip_dy = tip[1] - img_center[1]
    tgt_dx = target_canvas[0] - weapon_canvas[0]
    tgt_dy = target_canvas[1] - weapon_canvas[1]
    return math.degrees(
        math.atan2(tgt_dy, tgt_dx) - math.atan2(tip_dy, tip_dx)
    )


def rotate_point(pt: tuple, center: tuple, angle_deg: float) -> tuple:
    """Apply the same rotation PIL.rotate() uses; returns offset from center."""
    theta = math.radians(angle_deg)
    dx = pt[0] - center[0]
    dy = pt[1] - center[1]
    nx = dx * math.cos(theta) - dy * math.sin(theta)
    ny = dx * math.sin(theta) + dy * math.cos(theta)
    return round(nx), round(ny)


# ---------------------------------------------------------------------------
# Bézier helpers
# ---------------------------------------------------------------------------

def quad_ctrl(p0: tuple, p2: tuple, midpt: tuple) -> tuple:
    """Quadratic Bézier control point so B(0.5) == midpt."""
    return (
        round(2 * midpt[0] - 0.5 * (p0[0] + p2[0])),
        round(2 * midpt[1] - 0.5 * (p0[1] + p2[1])),
    )


def outward_ctrl(p0: tuple, p2: tuple, fraction: float = 0.35) -> tuple:
    """
    Control point for an outward-bowing arc.

    'Outward' = the perpendicular direction with the greater positive-x
    component (rightward, away from the weapon side of the card).
    """
    mid  = ((p0[0] + p2[0]) / 2, (p0[1] + p2[1]) / 2)
    cdx  = p2[0] - p0[0]
    cdy  = p2[1] - p0[1]
    clen = math.hypot(cdx, cdy) or 1.0
    px1, py1 = -cdy / clen,  cdx / clen   # CCW perp
    px2, py2 =  cdy / clen, -cdx / clen   # CW  perp
    px, py   = (px1, py1) if px1 >= px2 else (px2, py2)
    offset   = clen * fraction
    return (round(mid[0] + offset * px), round(mid[1] + offset * py))


def draw_quad_bezier(draw: ImageDraw.ImageDraw,
                     p0: tuple, p1: tuple, p2: tuple,
                     color: tuple, width: int,
                     steps: int = 120) -> None:
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1.0 - t
        x = round(u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0])
        y = round(u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1])
        pts.append((x, y))
    for i in range(len(pts) - 1):
        draw.line([pts[i], pts[i + 1]], fill=color, width=width)


# ---------------------------------------------------------------------------
# Line drawing
# ---------------------------------------------------------------------------

def draw_dashed_line(draw: ImageDraw.ImageDraw,
                     start: tuple, end: tuple,
                     color: tuple, width: int,
                     dash: int = 15, gap: int = 8) -> None:
    dx    = end[0] - start[0]
    dy    = end[1] - start[1]
    total = math.hypot(dx, dy)
    if total == 0:
        return
    ux, uy   = dx / total, dy / total
    pos      = 0.0
    is_dash  = True
    while pos < total:
        seg = dash if is_dash else gap
        if pos + seg > total:
            seg = total - pos
        if is_dash:
            a = (round(start[0] + pos * ux),       round(start[1] + pos * uy))
            b = (round(start[0] + (pos+seg) * ux), round(start[1] + (pos+seg) * uy))
            draw.line([a, b], fill=color, width=width)
        pos     += seg
        is_dash  = not is_dash


def draw_motion_lines(draw: ImageDraw.ImageDraw,
                      zone: str,
                      arc_start: tuple,
                      through_point: tuple,
                      weapon_center: tuple,
                      attack: int,
                      is_ranged: bool) -> None:
    """
    Draw motion lines for one attack zone.

    arc_start     – where melee arcs originate (weapon butt end, extended).
    through_point – for mid melee arcs, the point the Bézier passes through
                    (tip offset 10 % toward the butt).
    weapon_center – WEAPON_CENTER on the canvas; ranged rays start here.
    """
    target  = ZONE_CENTER[zone]
    color   = ZONE_COLOR[zone]
    n_lines = max(min(attack + 1, 4), 2)
    lw      = 3
    gap_px  = 7

    def perp_off(a: tuple, b: tuple, off: float) -> tuple:
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        ln = math.hypot(dx, dy) or 1.0
        return (-dy / ln * off, dx / ln * off)

    if is_ranged:
        base_ang  = math.atan2(target[1] - weapon_center[1],
                               target[0] - weapon_center[0])
        fan_total = math.radians(8)
        dist      = math.hypot(target[0] - weapon_center[0],
                               target[1] - weapon_center[1])
        for i in range(n_lines):
            frac = (i / (n_lines - 1) - 0.5) if n_lines > 1 else 0.0
            ang  = base_ang + fan_total * frac
            end  = (round(weapon_center[0] + dist * math.cos(ang)),
                    round(weapon_center[1] + dist * math.sin(ang)))
            draw_dashed_line(draw, weapon_center, end, color, lw)

    else:
        for i in range(n_lines):
            off = gap_px * (i - (n_lines - 1) / 2.0)
            po  = perp_off(arc_start, target, off)
            p0  = (round(arc_start[0] + po[0]), round(arc_start[1] + po[1]))
            p2  = (round(target[0]    + po[0]), round(target[1]    + po[1]))

            if zone == "mid":
                tp = (round(through_point[0] + po[0]), round(through_point[1] + po[1]))
                p1 = quad_ctrl(p0, p2, tp)
            else:
                # High and low: outward-bowing arc (rightward)
                p1 = outward_ctrl(p0, p2)

            draw_quad_bezier(draw, p0, p1, p2, color, lw)


# ---------------------------------------------------------------------------
# Per-row image generator
# ---------------------------------------------------------------------------

def generate_weapon_image(row: dict, output_path: str) -> None:
    group = row["Group"]
    name  = row["Name"]

    weapon_path = find_weapon_image(group)
    if not weapon_path:
        print(f"  SKIP  {group}/{name}: no matching weapon sprite")
        return

    # --- Load and resize weapon sprite ---
    base  = Image.open(weapon_path).convert("RGBA")
    w, h  = base.size
    scale = min(WEAPON_MAX_H / h, WEAPON_MAX_W / w, 1.0)
    new_w = round(w * scale)
    new_h = round(h * scale)
    base  = base.resize((new_w, new_h), Image.LANCZOS)

    # --- Locate tip and butt/handle ---
    tip    = find_tip(base)
    bottom = find_bottom(base)
    img_cx = new_w // 2
    img_cy = new_h // 2

    # Weapon span in (scaled) image pixels; rotation preserves length
    weapon_length = math.hypot(bottom[0] - tip[0], bottom[1] - tip[1])

    # --- Parse attacks and ranges ---
    attacks = {
        "low":  int(row.get("LowAttack")  or 0),
        "mid":  int(row.get("MidAttack")  or 0),
        "high": int(row.get("HighAttack") or 0),
    }
    ranges = {
        "low":  int(row.get("LowRange")  or 0),
        "mid":  int(row.get("MidRange")  or 0),
        "high": int(row.get("HighRange") or 0),
    }

    # --- Primary zone: highest count, tie-break low > mid > high ---
    max_atk      = max(attacks.values())
    primary_zone = None
    if max_atk > 0:
        for zone in ("low", "mid", "high"):
            if attacks[zone] == max_atk:
                primary_zone = zone
                break

    if primary_zone is None:
        # No attacks — present the weapon centred and unrotated, no motion lines.
        canvas  = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
        paste_x = WEAPON_CENTER[0] - new_w // 2
        paste_y = WEAPON_CENTER[1] - new_h // 2
        canvas.paste(base, (paste_x, paste_y), base)
        canvas.save(output_path)
        print(f"  OK    {group}/{name} → {output_path}  (no attacks, unrotated)")
        return

    # --- Rotation: first pass to find where the tip lands ---
    angle   = pil_rotation_deg(tip, (img_cx, img_cy),
                                ZONE_CENTER[primary_zone], WEAPON_CENTER)
    tip_rel = rotate_point(tip, (img_cx, img_cy), angle)

    # Shift short weapons right so the tip sits at TARGET_TIP_X.
    # Longer weapons whose tip already reaches there are left unchanged.
    x_shift       = max(0, TARGET_TIP_X - (WEAPON_CENTER[0] + tip_rel[0]))
    weapon_center = (WEAPON_CENTER[0] + x_shift, WEAPON_CENTER[1])

    # Recompute angle with the adjusted pivot (weapon still points at zone)
    angle   = pil_rotation_deg(tip, (img_cx, img_cy),
                                ZONE_CENTER[primary_zone], weapon_center)
    rotated = base.rotate(-angle, expand=True, resample=Image.BICUBIC)

    # --- Canvas positions of tip and butt after rotation ---
    tip_rel    = rotate_point(tip,    (img_cx, img_cy), angle)
    bottom_rel = rotate_point(bottom, (img_cx, img_cy), angle)
    tip_canvas    = (weapon_center[0] + tip_rel[0],    weapon_center[1] + tip_rel[1])
    bottom_canvas = (weapon_center[0] + bottom_rel[0], weapon_center[1] + bottom_rel[1])

    # Unit vector from tip toward butt (direction "back along shaft")
    tb = (bottom_canvas[0] - tip_canvas[0], bottom_canvas[1] - tip_canvas[1])
    tb_len  = math.hypot(*tb) or 1.0
    tb_unit = (tb[0] / tb_len, tb[1] / tb_len)

    # Arc start: butt end extended 10 % past the handle
    arc_start = (
        round(bottom_canvas[0] + 0.1 * weapon_length * tb_unit[0]),
        round(bottom_canvas[1] + 0.1 * weapon_length * tb_unit[1]),
    )

    # Through-point for mid arcs: 10 % back from tip along the shaft
    through_point = (
        round(tip_canvas[0] + 0.1 * weapon_length * tb_unit[0]),
        round(tip_canvas[1] + 0.1 * weapon_length * tb_unit[1]),
    )

    # --- Build image: motion layer behind weapon ---
    canvas       = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    motion_layer = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    motion_draw  = ImageDraw.Draw(motion_layer)

    for zone, atk in attacks.items():
        if atk > 0:
            draw_motion_lines(
                motion_draw, zone,
                arc_start, through_point, weapon_center,
                atk, is_ranged=(ranges[zone] > 0),
            )

    canvas = Image.alpha_composite(canvas, motion_layer)

    paste_x = weapon_center[0] - rotated.width  // 2
    paste_y = weapon_center[1] - rotated.height // 2
    canvas.paste(rotated, (paste_x, paste_y), rotated)

    canvas.save(output_path)
    print(f"  OK    {group}/{name} → {output_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    csv_path   = "Weapon actions.csv"
    output_dir = "pictures/weapon_pictures"
    os.makedirs(output_dir, exist_ok=True)

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if int(row.get("PrintID") or 0) == 0:
                continue
            out_name = os.path.basename(row["CardImg"])
            out_path = os.path.join(output_dir, out_name)
            print(f"Generating {row['Group']}/{row['Name']} …")
            generate_weapon_image(row, out_path)


if __name__ == "__main__":
    main()
