#!/usr/bin/env python3
"""
generate_weapon_images.py

For each row in Weapon actions.csv, generates a 640×890 RGBA weapon
illustration in pictures/weapon_pictures/.

The base weapon sprite (pictures/weapons/<group>.png) is scaled, then
rotated so its topmost non-transparent pixel (the tip) points toward
the primary attack zone.

Motion lines are drawn BEHIND the weapon.  A single tapered arc is drawn
for melee attacks (thick at its origin, thin at the weapon tip); the shape
depends on which attack zones are active and which is primary:
  - Single zone: arc from the card's left edge at that zone's height,
    curving gently toward the tip.
  - Multi-zone combinations: originate from geometric anchors (centre,
    corners, left/right mid-points) chosen to convey the swing direction.
  - Two arcs are drawn for high/low combos and the full-3-zone mid-primary case.
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
TARGET_TIP_X  = 460

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


def draw_quad_bezier_tapered(draw: ImageDraw.ImageDraw,
                              p0: tuple, p1: tuple, p2: tuple,
                              color: tuple,
                              width_start: int = 14, width_end: int = 3,
                              steps: int = 120) -> None:
    """Quadratic Bézier with width tapering from width_start at p0 to width_end at p2."""
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1.0 - t
        x = round(u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0])
        y = round(u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1])
        pts.append((x, y))
    for i in range(len(pts) - 1):
        t = i / steps
        w = max(round(width_start + t * (width_end - width_start)), 1)
        draw.line([pts[i], pts[i + 1]], fill=color, width=w)


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


def draw_ranged_lines(draw: ImageDraw.ImageDraw,
                      zone: str,
                      weapon_center: tuple,
                      attack: int) -> None:
    """Dashed-ray fan for a ranged attack zone."""
    target    = ZONE_CENTER[zone]
    color     = ZONE_COLOR[zone]
    n_lines   = max(min(attack + 1, 4), 2)
    lw        = 3
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


def draw_melee_arcs(draw: ImageDraw.ImageDraw,
                    tip_canvas: tuple,
                    melee_attacks: dict,
                    primary_zone: str) -> None:
    """
    Draw a single tapered melee motion arc (or a pair for certain zone combos).

    The arc shape is determined by which zones are active and which is primary.
    All arcs taper from thick at their origin to thin at the weapon tip.
    melee_attacks: {zone: atk_count} for melee-only zones (range == 0).
    """
    active = [z for z in ("high", "mid", "low") if melee_attacks.get(z, 0) > 0]
    if not active:
        return

    color       = ZONE_COLOR[primary_zone]
    tip         = tip_canvas
    W_START     = 14
    W_END       = 3

    # Named anchor positions on the canvas
    L  = 50
    CX = CARD_W // 2            # 320
    HY = ZONE_CENTER["high"][1]  # ≈ 245
    MY = ZONE_CENTER["mid"][1]   # ≈ 460
    LY = ZONE_CENTER["low"][1]   # ≈ 675

    top_left  = (L,   HY)
    mid_left  = (L,   MY)
    bot_left  = (L,   LY)
    top_ctr   = (CX,  HY)
    center    = (CX,  MY)
    bot_ctr   = (CX,  LY)
    top_right = (420, HY)
    mid_right = (420, MY)
    bot_right = (420, LY)

    def arc(p0, through, p2=None):
        """Tapered arc from p0, passing through `through` at t=0.5, ending at p2."""
        if p2 is None:
            p2 = tip
        p1 = quad_ctrl(p0, p2, through)
        draw_quad_bezier_tapered(draw, p0, p1, p2, color, W_START, W_END)

    def arc_outward(p0, p2=None, fraction=0.35):
        """Tapered arc that bows outward (rightward)."""
        if p2 is None:
            p2 = tip
        p1 = outward_ctrl(p0, p2, fraction)
        draw_quad_bezier_tapered(draw, p0, p1, p2, color, W_START, W_END)

    def mp(p0, p2, dx=0, dy=0):
        """Midpoint of p0→p2 plus an absolute pixel offset."""
        return (round((p0[0] + p2[0]) / 2 + dx), round((p0[1] + p2[1]) / 2 + dy))

    # -----------------------------------------------------------------------
    # Single active zone
    # -----------------------------------------------------------------------
    if active == ["high"]:
        # Arc from top-left curving over (upward) to tip
        arc(top_left, mp(top_left, tip, dy=-140))

    elif active == ["mid"]:
        # Arc from mid-left curving up to tip
        arc(mid_left, mp(mid_left, tip, dy=-110))

    elif active == ["low"]:
        # Arc from bottom-left curving down to tip
        arc(bot_left, mp(bot_left, tip, dy=110))

    # -----------------------------------------------------------------------
    # Two active zones
    # -----------------------------------------------------------------------
    elif active == ["high", "mid"] and primary_zone == "high":
        # Arc curves up from bottom-middle, through mid-right, to tip
        arc(bot_ctr, mid_right)

    elif active == ["high", "mid"] and primary_zone == "mid":
        # Arc from top-left, through top-right area, to tip
        arc(top_left, (380, HY - 40))

    elif active == ["high", "low"] and primary_zone == "high":
        # Two arcs from center, both curving upward to tip
        arc(center, (360, 240))
        arc(center, (440, 240))

    elif active == ["high", "low"] and primary_zone == "low":
        # Two arcs from center, both curving downward to tip
        arc(center, (360, 660))
        arc(center, (440, 660))

    elif active == ["mid", "low"] and primary_zone == "mid":
        # Arc from bottom-left, through bottom-right mark, to tip
        arc(bot_left, bot_right)

    elif active == ["mid", "low"] and primary_zone == "low":
        # Arc from middle-left, through middle-right, to tip
        arc(mid_left, mid_right)

    # -----------------------------------------------------------------------
    # Three active zones
    # -----------------------------------------------------------------------
    elif active == ["high", "mid", "low"] and primary_zone == "mid":
        # Two arcs: one from top-center, one from bottom-center, both to tip
        arc_outward(top_ctr)
        arc_outward(bot_ctr)

    elif active == ["high", "mid", "low"] and primary_zone == "high":
        # Arc from bottom-right to tip
        arc(bot_right, mp(bot_right, tip, dx=30))

    elif active == ["high", "mid", "low"] and primary_zone == "low":
        # Arc from top-right to tip
        arc(top_right, mp(top_right, tip, dx=30))

    else:
        # Fallback: outward arc from the primary zone's left anchor
        left_pts = {"high": top_left, "mid": mid_left, "low": bot_left}
        arc_outward(left_pts.get(primary_zone, mid_left))


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
    img_cx = new_w // 2
    img_cy = new_h // 2

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

    # --- Primary zone: highest count, tie-break  mid > low >high ---
    max_atk      = max(attacks.values())
    primary_zone = None
    if max_atk > 0:
        for zone in ("mid", "low", "high"):
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

    # Shift short weapons so the tip sits at TARGET_TIP_X.
    # The shift is applied along the weapon's own butt→tip axis direction so
    # that it behaves like a pre-rotation vertical offset (sliding the weapon
    # forward along its shaft, not purely sideways on the canvas).
    tip_len = math.hypot(*tip_rel) or 1.0
    bt_unit = (tip_rel[0] / tip_len, tip_rel[1] / tip_len)

    weapon_center = WEAPON_CENTER
    current_tip_x = WEAPON_CENTER[0] + tip_rel[0]
    if current_tip_x < TARGET_TIP_X and abs(bt_unit[0]) > 1e-6:
        d = (TARGET_TIP_X - current_tip_x) / bt_unit[0]
        if d > 0:
            weapon_center = (
                round(WEAPON_CENTER[0] + d * bt_unit[0]),
                round(WEAPON_CENTER[1] + d * bt_unit[1]),
            )

    # Recompute angle with the adjusted pivot (weapon still points at zone)
    angle   = pil_rotation_deg(tip, (img_cx, img_cy),
                                ZONE_CENTER[primary_zone], weapon_center)
    rotated = base.rotate(-angle, expand=True, resample=Image.BICUBIC)

    # --- Canvas position of tip after rotation ---
    tip_rel    = rotate_point(tip, (img_cx, img_cy), angle)
    tip_canvas = (weapon_center[0] + tip_rel[0], weapon_center[1] + tip_rel[1])

    # --- Build image: motion layer behind weapon ---
    canvas       = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    motion_layer = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    motion_draw  = ImageDraw.Draw(motion_layer)

    # Ranged zones: dashed ray fans
    for zone, atk in attacks.items():
        if atk > 0 and ranges[zone] > 0:
            draw_ranged_lines(motion_draw, zone, weapon_center, atk)

    # Melee zones: single tapered arc (or pair for certain combos)
    melee_attacks = {z: v for z, v in attacks.items() if v > 0 and ranges[z] == 0}
    if melee_attacks:
        max_m = max(melee_attacks.values())
        melee_primary = None
        for z in ("mid", "low", "high"):
            if melee_attacks.get(z, 0) == max_m:
                melee_primary = z
                break
        draw_melee_arcs(motion_draw, tip_canvas, melee_attacks, melee_primary)

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
