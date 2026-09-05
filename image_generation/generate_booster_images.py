#!/usr/bin/env python3
"""
generate_booster_images.py

For each row in "Booster actions.csv", generates a 640×890 RGBA image in
pictures/booster_pictures/.  Each image places a randomly-chosen foreground
mech figure (from pictures/foreground/, excluding collective figures) over a
layered rocket-engine flare:  yellow-orange jagged outer burst → orange mid
burst → blue plasma → white-blue hot core.

Output filenames: Booster_<sanitised_name>.png -- capitalised to match the
CardImg column in "Booster actions.csv" (booster_pictures/Booster_<name>.png)
and the rest of the card art. They used to be written lowercase, which only
worked because TeX Live's kpathsea quietly falls back to a case-folded search.
"""

import csv
import math
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CARD_W = 640
CARD_H = 890

_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)

FOREGROUND_DIR = os.path.join(_PROJECT_ROOT, "pictures", "foreground")
OUTPUT_DIR     = os.path.join(_PROJECT_ROOT, "pictures", "booster_pictures")
BOOSTER_CSV    = os.path.join(_PROJECT_ROOT, "Booster actions.csv")

# Foreground images whose names start with this prefix are excluded.
EXCLUDE_PREFIX = "collective_"

# Mech vertical centre: 42 % down (slightly above card centre).
MECH_CY_FRAC = 0.42

# Mech max width on canvas (px); height is constrained proportionally.
MECH_MAX_W = 560
MECH_MAX_H = 700


# ---------------------------------------------------------------------------
# Foreground catalogue
# ---------------------------------------------------------------------------

def get_foreground_images() -> list[str]:
    paths = []
    for fname in sorted(os.listdir(FOREGROUND_DIR)):
        if fname.startswith(EXCLUDE_PREFIX):
            continue
        if fname.lower().endswith((".png", ".jpg", ".jpeg")):
            paths.append(os.path.join(FOREGROUND_DIR, fname))
    return paths


# ---------------------------------------------------------------------------
# Flare helpers
# ---------------------------------------------------------------------------

def jagged_polygon(
    cx: float, cy: float,
    n_spikes: int,
    outer_r: float, inner_r: float,
    rng: np.random.RandomState,
    stretch_y: float = 1.0,
    angle_offset: float = 0.0,
) -> list[tuple[float, float]]:
    """Star/burst polygon with randomised tip and valley radii."""
    pts = []
    total = n_spikes * 2
    for i in range(total):
        angle = angle_offset + (i / total) * 2 * math.pi
        if i % 2 == 0:   # spike tip
            r = outer_r * (0.80 + 0.20 * rng.random())
        else:             # valley between spikes
            r = inner_r * (0.75 + 0.25 * rng.random())
        pts.append((
            cx + r * math.cos(angle),
            cy + r * math.sin(angle) * stretch_y,
        ))
    return pts


def add_blurred_polygon(
    base: Image.Image,
    pts: list[tuple[float, float]],
    fill: tuple[int, int, int, int],
    blur: float,
) -> Image.Image:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).polygon(pts, fill=fill)
    if blur > 0:
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
    return Image.alpha_composite(base, layer)


def add_blurred_ellipse(
    base: Image.Image,
    bbox: tuple[int, int, int, int],
    fill: tuple[int, int, int, int],
    blur: float,
) -> Image.Image:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(bbox, fill=fill)
    if blur > 0:
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
    return Image.alpha_composite(base, layer)


def generate_flare(rng: np.random.RandomState, cx: int, cy: int) -> Image.Image:
    """Return a 640×890 RGBA image containing the rocket-engine flare centred at (cx, cy)."""
    flare  = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))

    # ── Outermost burst: broad yellow-gold glow ──────────────────────────────
    pts = jagged_polygon(cx, cy, n_spikes=20, outer_r=295, inner_r=165,
                         rng=rng, stretch_y=1.25)
    flare = add_blurred_polygon(flare, pts, (255, 215, 0, 160), blur=28)

    # ── Second burst: tighter orange spike ring ───────────────────────────────
    pts = jagged_polygon(cx, cy, n_spikes=16, outer_r=210, inner_r=115,
                         rng=rng, stretch_y=1.15, angle_offset=0.20)
    flare = add_blurred_polygon(flare, pts, (255, 130, 10, 195), blur=16)

    # ── Third burst: sharp yellow-white inner spikes ─────────────────────────
    pts = jagged_polygon(cx, cy, n_spikes=12, outer_r=145, inner_r=75,
                         rng=rng, stretch_y=1.05, angle_offset=0.55)
    flare = add_blurred_polygon(flare, pts, (255, 245, 130, 220), blur=10)

    # ── Blue plasma region ───────────────────────────────────────────────────
    pts = jagged_polygon(cx, cy, n_spikes=9, outer_r=95, inner_r=58,
                         rng=rng, stretch_y=1.0, angle_offset=0.90)
    flare = add_blurred_polygon(flare, pts, (20, 80, 255, 235), blur=20)

    # ── Bright blue-white core ───────────────────────────────────────────────
    flare = add_blurred_ellipse(
        flare, (cx - 48, cy - 58, cx + 48, cy + 58),
        (150, 210, 255, 255), blur=22,
    )

    # ── White hotspot ────────────────────────────────────────────────────────
    flare = add_blurred_ellipse(
        flare, (cx - 20, cy - 24, cx + 20, cy + 24),
        (240, 248, 255, 255), blur=10,
    )

    return flare


# ---------------------------------------------------------------------------
# Foreground placement
# ---------------------------------------------------------------------------

def alpha_weighted_centroid(img: Image.Image) -> tuple[float, float]:
    """Return (cx, cy) in image-pixel coordinates, weighted by alpha channel."""
    arr   = np.array(img.convert("RGBA"))
    alpha = arr[:, :, 3].astype(np.float64)
    total = alpha.sum()
    if total == 0:
        return img.width / 2.0, img.height / 2.0
    ys, xs = np.mgrid[0:img.height, 0:img.width]
    cx = float((xs * alpha).sum() / total)
    cy = float((ys * alpha).sum() / total)
    return cx, cy


def place_foreground(fg_path: str) -> tuple[Image.Image, tuple[int, int]]:
    """
    Scale and centre the mech on a 640×890 RGBA canvas.

    Returns (canvas, (body_cx, body_cy)) where body_* is the mech's
    alpha-weighted centroid mapped to canvas pixel coordinates.
    """
    fg = Image.open(fg_path).convert("RGBA")

    # Body centre in original image coordinates
    orig_cx, orig_cy = alpha_weighted_centroid(fg)

    scale = min(MECH_MAX_W / fg.width, MECH_MAX_H / fg.height, 1.0)
    new_w = round(fg.width  * scale)
    new_h = round(fg.height * scale)
    fg    = fg.resize((new_w, new_h), Image.LANCZOS)

    # Place so the mech's geometric centre is at MECH_CY_FRAC of card height.
    target_cy = int(CARD_H * MECH_CY_FRAC)
    paste_x   = (CARD_W - new_w) // 2
    paste_y   = target_cy - new_h // 2

    # Map original centroid → canvas coordinates
    body_cx = round(paste_x + orig_cx * scale)
    body_cy = round(paste_y + orig_cy * scale)

    canvas = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    canvas.paste(fg, (paste_x, paste_y), fg)
    return canvas, (body_cx, body_cy)


# ---------------------------------------------------------------------------
# Per-card generator
# ---------------------------------------------------------------------------

def sanitise(name: str) -> str:
    return name.replace(" ", "_").replace("/", "-")


def generate_booster_image(name: str, fg_path: str, out_path: str) -> None:
    seed = hash(name) & 0x7FFF_FFFF
    rng  = np.random.RandomState(seed)

    fg_layer, (body_cx, body_cy) = place_foreground(fg_path)
    flare = generate_flare(rng, body_cx, body_cy)

    canvas = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    canvas = Image.alpha_composite(canvas, flare)
    canvas = Image.alpha_composite(canvas, fg_layer)

    canvas.save(out_path)
    print(f"  OK    {name!r:30s} fg={os.path.basename(fg_path):25s} "
          f" body=({body_cx},{body_cy})  → {out_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    fg_images = get_foreground_images()
    if not fg_images:
        raise RuntimeError(f"No foreground images found in {FOREGROUND_DIR!r}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(BOOSTER_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if int(row.get("PrintID") or 0) == 0:
                continue
            name    = row["Name"]
            # Seed selection with card name so the same card always picks the
            # same foreground, but different cards get different figures.
            fg_path = random.Random(hash(name)).choice(fg_images)
            out     = os.path.join(OUTPUT_DIR, f"Booster_{sanitise(name)}.png")
            print(f"Generating {name!r} …")
            generate_booster_image(name, fg_path, out)

    print("\nDone.  CardImg in 'Booster actions.csv' reads e.g.:")
    print("  booster_pictures/Booster_<Name>.png")


if __name__ == "__main__":
    main()
