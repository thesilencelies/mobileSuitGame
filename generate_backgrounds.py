import cv2
import numpy as np
import csv
import os
import math
import hashlib

WIDTH, HEIGHT = 896, 1280


def group_hue(group_name: str) -> int:
    """Deterministic hue (0–179 for OpenCV) from group name."""
    return int(hashlib.md5(group_name.encode()).hexdigest()[:4], 16) % 180


def generate_background(group_name: str, out_path: str) -> None:
    hue = group_hue(group_name)

    # Dark, muted base colour
    base_bgr = cv2.cvtColor(
        np.uint8([[[hue, 50, 35]]]), cv2.COLOR_HSV2BGR
    )[0][0].tolist()

    # Primary streak: same hue, a bit brighter/more saturated but still soft
    streak_bgr = cv2.cvtColor(
        np.uint8([[[hue, 85, 120]]]), cv2.COLOR_HSV2BGR
    )[0][0].tolist()

    # Secondary streak: analogous hue (~30° shift), slightly dimmer — minor accent
    hue2 = (hue + 30) % 180
    streak2_bgr = cv2.cvtColor(
        np.uint8([[[hue2, 70, 95]]]), cv2.COLOR_HSV2BGR
    )[0][0].tolist()

    # Work at 1/4 resolution — the upscale step adds blur for free
    scale = 4
    sw, sh = WIDTH // scale, HEIGHT // scale
    scx, scy = sw // 2, sh // 2

    canvas = np.full((sh, sw, 3), base_bgr, dtype=np.uint8)

    # Deterministic RNG seeded from group name
    seed = int(hashlib.md5(group_name.encode()).hexdigest()[4:12], 16) % (2**32)
    rng = np.random.default_rng(seed)

    # Radial streaks radiating from centre
    n_streaks = 120
    angles = rng.uniform(0, 2 * math.pi, n_streaks)
    lengths = rng.uniform(sh * 0.25, sh * 0.9, n_streaks)
    widths = rng.integers(2, 10, n_streaks)

    # ~25 % of streaks use the secondary tone; keep them narrower
    use_secondary = rng.random(n_streaks) < 0.25

    for angle, length, w, secondary in zip(angles, lengths, widths, use_secondary):
        color = streak2_bgr if secondary else streak_bgr
        line_w = max(1, int(w) - 2) if secondary else int(w)
        x2 = int(scx + length * math.cos(angle))
        y2 = int(scy + length * math.sin(angle))
        cv2.line(canvas, (scx, scy), (x2, y2), color, line_w)

    # Blur at low resolution (equivalent to ~200 px sigma at full res)
    canvas = cv2.GaussianBlur(canvas, (51, 51), 0)

    # Upscale — bilinear interpolation adds additional softness
    canvas = cv2.resize(canvas, (WIDTH, HEIGHT), interpolation=cv2.INTER_LINEAR)

    # Light finishing blur
    canvas = cv2.GaussianBlur(canvas, (21, 21), 0)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cv2.imwrite(out_path, canvas)
    print(f"  {group_name:20s} -> {out_path}")


def main() -> None:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(script_dir, "Weapon actions.csv")

    groups: set[str] = set()
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            g = row["Group"].strip()
            if g:
                groups.add(g)

    print(f"Generating backgrounds for {len(groups)} groups …")
    for group in sorted(groups):
        safe = group.replace(" ", "_")
        out_path = os.path.join(
            script_dir, "pictures", "backgrounds", f"{safe}_bg.png"
        )
        generate_background(group, out_path)

    print("Done.")


if __name__ == "__main__":
    main()
