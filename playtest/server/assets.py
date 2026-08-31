"""Battlefield art: terrain cards and objective tokens, sized for a phone.

Card art has `images.py`. This module is everything else the board draws:

* **Terrain** -- the printed terrain cards are 640x890 PNGs in `terrain/` at the
  repo root, and the board wants only the *playable grid* out of them. The card
  is a 6.4 x 8.9 cm rectangle with the art placed at 6.35 cm wide, centred at
  (3.25, 4.45); the 3 x 4 grid of 2.06 cm tiles is laid at (0.1, 0.2) upwards
  (`terrain_cards.py`). Cropping to exactly that grid gives an image whose
  pixels line up with the board's tiles, so the client can blit one card across
  its 3 x 4 tile block with no fudge factors. The crop comes out 3:4 to within a
  pixel, which is the check that the geometry below is right.

* **Tokens** -- `tts_assets/*.png`, 600 x 600 with transparency. The numbered
  ones encode remaining hit points (`Tower4` .. `Tower1`, `PowerPlant2` ..
  `PowerPlant1`), so the board can show an objective's damage state by picking
  the file rather than by drawing a number on a disc.

* **Frames** -- the standees. `Frames.csv` points each frame at its artwork in
  `pictures/foreground/`; `-trim` cuts the mech out of its transparent canvas
  so the board can stand it on the bottom edge of its tile instead of drawing
  a counter with three letters on it.

All three are downscaled into `static/` and **committed**: the phone clones the
repo and has neither ImageMagick nor the originals, so art that ships must be
tracked (see `.gitignore`).

    python -m playtest.server.assets            # build all three
    python -m playtest.server.assets --force
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Mapping, Optional

from .images import REPO_ROOT, _files, _imagemagick

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Where the printed terrain art lives (`CardImg` in `Terrain_square.csv`).
TERRAIN_SOURCE = REPO_ROOT / "terrain"
TERRAIN_DIR = STATIC_DIR / "terrain"

#: Tabletop Simulator piece art -- the objective tokens.
TOKEN_SOURCE = REPO_ROOT / "tts_assets"
TOKEN_DIR = STATIC_DIR / "tokens"

#: The tile glyphs the printed terrain cards stamp in a tile's corner
#: (`terrain_cards.py`: the `icon` of each entry in `STYLE_DICT`). The board
#: draws the same ones, so a rooftop or a blocked tile on screen is marked the
#: way it is marked on the card.
TILE_ICON_SOURCE = REPO_ROOT / "icons"
TILE_DIR = STATIC_DIR / "tiles"

#: The frame artwork `Frames.csv` points at (`CardImg`, e.g.
#: `foreground/ouwa_kuwagata.png`), relative to `pictures/`. These are the
#: standees: the board draws the mech itself standing on its tile rather than
#: an abstract counter, which is the difference between "a blue box is at
#: (7,12)" and "Kuwagata is at (7,12)".
FRAME_SOURCE = REPO_ROOT / "pictures"
FRAME_DIR = STATIC_DIR / "frames"

#: Terrain is drawn at most one card per 3 x 4 tiles. A tile is ~52 CSS px at
#: the client's tactical zoom, so 3 tiles is ~156 px; 240 keeps it sharp at 1.5x
#: without the bundle getting silly.
TERRAIN_WIDTH = 240
TERRAIN_QUALITY = 78

#: A token is drawn inside one tile.
TOKEN_WIDTH = 96

#: A tile glyph is drawn about a third of a tile wide -- the card puts it at
#: 0.6 cm in a 2.06 cm tile. That is ~17 px at the tactical zoom, so 64 keeps
#: it sharp on a 3x phone and the three files come to about 3 kB.
TILE_ICON_WIDTH = 64

#: The glyph files: the three elevation levels, impassable, obstacle,
#: objective and token spawn. Ground has none. Every code the printed card
#: stamps a glyph for is here -- the board draws the card's marking, not an
#: overlay of its own.
TILE_ICON_FILES: tuple[str, ...] = (
    "e1", "e2", "e3", "imp", "obs", "obj", "tkn",
)

#: A standee is drawn about one and a sixth tiles tall, anchored to the bottom
#: of its tile. A tile is ~52 CSS px at the tactical zoom, so ~60 px tall; 128
#: keeps it sharp at 1.5x on a 3x phone and the whole set is under 60 kB.
FRAME_WIDTH = 128

#: The token art the board uses, by file stem. The numbered ones are hit-point
#: states, so `Tower4` is an undamaged Tower and `Tower1` is one hit from gone.
TOKEN_FILES: tuple[str, ...] = (
    "PowerPlant1", "PowerPlant2",
    "Tower1", "Tower2", "Tower3", "Tower4",
    "Shiny", "Fugitive", "Barricade", "GravityWell",
    "Portal", "Illusion", "Real", "Image",
    "Cage", "Rebound", "Storm",
)

#: Tokens whose art is not a Tabletop Simulator piece. A summoned drone is a
#: game piece on this board but has never needed one for TTS, so it borrows the
#: card's own artwork -- trimmed out of its transparent canvas the same way a
#: standee is, so it stands on its tile rather than filling it edge to edge.
TOKEN_EXTRA_SOURCES: Mapping[str, tuple[str, ...]] = {
    "Swarm": ("pictures/Swarm.png",),
}

# --------------------------------------------------------------------------
# Terrain geometry (mirrors terrain_cards.py:create_terrain_card)
# --------------------------------------------------------------------------

#: The card's own extent and where the background art is placed on it, in cm.
ART_MAX_W_CM = 6.35
ART_MAX_H_CM = 8.85
ART_CENTRE_CM = (3.25, 4.45)

#: The playable grid: `CARD_COLS x CARD_ROWS` squares of `TILE_CM`, with its
#: bottom-left corner at `GRID_ORIGIN_CM` (TikZ y grows upwards).
TILE_CM = 2.06
GRID_ORIGIN_CM = (0.1, 0.2)
GRID_COLS = 3
GRID_ROWS = 4


def grid_crop(width: int, height: int) -> tuple[int, int, int, int]:
    """`(w, h, x, y)` in pixels of the playable grid inside a terrain image.

    `keepaspectratio` means the art is width-limited unless that would make it
    taller than the card, in which case it is height-limited -- three of the
    shipped files are 640x898 and take the second branch.
    """
    art_w = min(ART_MAX_W_CM, ART_MAX_H_CM * width / height)
    art_h = art_w * height / width
    left = ART_CENTRE_CM[0] - art_w / 2
    top = ART_CENTRE_CM[1] + art_h / 2          # TikZ y-up, so this is the top

    gx0 = GRID_ORIGIN_CM[0]
    gx1 = gx0 + GRID_COLS * TILE_CM
    gy0 = GRID_ORIGIN_CM[1]
    gy1 = gy0 + GRID_ROWS * TILE_CM

    x = round((gx0 - left) / art_w * width)
    right = round((gx1 - left) / art_w * width)
    y = round((top - gy1) / art_h * height)
    bottom = round((top - gy0) / art_h * height)

    x = max(0, min(width - 1, x))
    y = max(0, min(height - 1, y))
    return (max(1, min(width - x, right - x)),
            max(1, min(height - y, bottom - y)), x, y)


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------


def slug(name: str) -> str:
    """`"Power Reactors"` -> `"Power_Reactors"`, the client's filename.

    Terrain card names come off the CSV with spaces in them, and a board that
    fetched them URL-encoded through an API route would be 20 requests deep on
    every game. They are static files instead, so the name has to survive as a
    filename.
    """
    return re.sub(r"[^A-Za-z0-9]+", "_", (name or "").strip()).strip("_")


_TERRAIN_INDEX: Optional[dict[str, Path]] = None


def terrain_files() -> dict[str, Path]:
    """`{card name: bundled file}` for every terrain card art exists for.

    Cached: this parses the terrain CSV, and the bundle only changes when the
    builder below runs (which clears the cache).
    """
    global _TERRAIN_INDEX
    if _TERRAIN_INDEX is None:
        from ..engine.terrain import load_terrain_cards

        out: dict[str, Path] = {}
        for name in load_terrain_cards():
            target = TERRAIN_DIR / f"{slug(name)}.jpg"
            if target.is_file():
                out[name] = target
        _TERRAIN_INDEX = out
    return dict(_TERRAIN_INDEX)


def refresh() -> None:
    """Forget the bundle index (the builder just wrote to it)."""
    global _TERRAIN_INDEX
    _TERRAIN_INDEX = None


def token_files() -> list[str]:
    return sorted(p.stem for p in _files(TOKEN_DIR))


def frame_files() -> dict[str, Path]:
    """`{frame name: bundled standee}` for every frame art exists for.

    Keyed by the frame's *name* rather than its art filename, because that is
    what the client has: a frame in the view carries `name`, and the board
    turns that straight into `/static/frames/<slug>.png`.
    """
    from ..engine.cards import load_frames

    out: dict[str, Path] = {}
    for name in load_frames():
        target = FRAME_DIR / f"{slug(name)}.png"
        if target.is_file():
            out[name] = target
    return out


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------


def _run(argv: list[str]) -> bool:
    try:
        subprocess.run(argv, check=True, capture_output=True, timeout=120)
    except Exception:
        return False
    return True


def _dimensions(tool: str, path: Path) -> Optional[tuple[int, int]]:
    identify = Path(tool).with_name("identify")
    argv = ([str(identify), "-format", "%w %h", str(path)] if identify.exists()
            else [tool, "identify", "-format", "%w %h", str(path)])
    try:
        out = subprocess.run(argv, check=True, capture_output=True, timeout=60)
    except Exception:
        return None
    try:
        w, h = out.stdout.decode().strip().split()[:2]
        return int(w), int(h)
    except (ValueError, IndexError):
        return None


def _convert_argv(tool: str) -> list[str]:
    argv = [tool]
    if Path(tool).name == "magick":
        argv.append("convert")
    return argv


def build_terrain(
    source: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    *,
    width: int = TERRAIN_WIDTH,
    force: bool = False,
    quiet: bool = False,
) -> dict[str, object]:
    """Crop every terrain card to its playable grid and downscale it."""
    from ..engine.terrain import load_terrain_cards

    source = Path(source) if source else TERRAIN_SOURCE
    out_dir = Path(out_dir) if out_dir else TERRAIN_DIR
    tool = _imagemagick()
    if tool is None:
        raise RuntimeError(
            "ImageMagick (`convert` or `magick`) is needed to build the terrain "
            "bundle. Build it on a desktop, then copy the repo to the phone."
        )
    cards = load_terrain_cards()
    out_dir.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    missing: list[str] = []
    failed: list[str] = []
    wanted: set[str] = set()
    for name, card in sorted(cards.items()):
        art = source / card.image if card.image else None
        if art is None or not art.is_file():
            missing.append(name)
            continue
        target = out_dir / f"{slug(name)}.jpg"
        wanted.add(target.name)
        if target.is_file() and not force \
                and target.stat().st_mtime_ns >= art.stat().st_mtime_ns:
            skipped += 1
            continue
        size = _dimensions(tool, art)
        if size is None:
            failed.append(name)
            continue
        cw, ch, cx, cy = grid_crop(*size)
        argv = _convert_argv(tool) + [
            str(art),
            "-crop", f"{cw}x{ch}+{cx}+{cy}", "+repage",
            "-resize", f"{width}x{round(width * GRID_ROWS / GRID_COLS)}!",
            "-strip", "-background", "white", "-flatten",
            "-interlace", "Plane", "-quality", str(TERRAIN_QUALITY),
            str(target),
        ]
        if _run(argv) and target.is_file():
            written += 1
        else:
            failed.append(name)
    removed = 0
    for path in _files(out_dir):
        if path.name not in wanted:
            path.unlink()
            removed += 1
    refresh()
    summary = _summary("terrain", out_dir, written, skipped, removed, failed)
    summary["missingArt"] = missing
    if not quiet:
        _report(summary)
    return summary


def build_tokens(
    source: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    *,
    width: int = TOKEN_WIDTH,
    force: bool = False,
    quiet: bool = False,
) -> dict[str, object]:
    """Downscale the token art, keeping its transparency."""
    source = Path(source) if source else TOKEN_SOURCE
    out_dir = Path(out_dir) if out_dir else TOKEN_DIR
    tool = _imagemagick()
    if tool is None:
        raise RuntimeError("ImageMagick is needed to build the token bundle.")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    failed: list[str] = []
    wanted: set[str] = set()
    jobs: list[tuple[str, Path, bool]] = [
        (stem, source / f"{stem}.png", False) for stem in TOKEN_FILES
    ]
    for stem, candidates in TOKEN_EXTRA_SOURCES.items():
        for relative in candidates:
            art = REPO_ROOT / relative
            if art.is_file():
                jobs.append((stem, art, True))
                break
        else:
            failed.append(stem)
    for stem, art, trim in jobs:
        if not art.is_file():
            failed.append(stem)
            continue
        target = out_dir / f"{stem}.png"
        wanted.add(target.name)
        if target.is_file() and not force \
                and target.stat().st_mtime_ns >= art.stat().st_mtime_ns:
            skipped += 1
            continue
        # PNG8 with a 128-colour palette: these are flat illustrations, and the
        # alpha channel has to survive so the tile shows through behind them.
        argv = _convert_argv(tool) + [str(art)]
        if trim:
            argv += ["-trim", "+repage"]
        argv += [
            "-resize", f"{width}x{width}>", "-strip",
            "-colors", "128", "-define", "png:compression-level=9",
            f"PNG8:{target}",
        ]
        if _run(argv) and target.is_file():
            written += 1
        else:
            failed.append(stem)
    removed = 0
    for path in _files(out_dir):
        if path.name not in wanted:
            path.unlink()
            removed += 1
    summary = _summary("tokens", out_dir, written, skipped, removed, failed)
    if not quiet:
        _report(summary)
    return summary


def build_frames(
    source: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    *,
    width: int = FRAME_WIDTH,
    force: bool = False,
    quiet: bool = False,
) -> dict[str, object]:
    """Cut each frame's artwork out of its background and shrink it to a standee.

    `-trim` is what makes this a standee rather than a picture: the source art
    is a mech on a large transparent canvas, and the board needs the mech's own
    bounding box so it can stand it on the bottom edge of its tile. The alpha
    channel has to survive, so this is PNG8 with a palette, like the tokens.
    """
    from ..engine.cards import load_frames

    source = Path(source) if source else FRAME_SOURCE
    out_dir = Path(out_dir) if out_dir else FRAME_DIR
    tool = _imagemagick()
    if tool is None:
        raise RuntimeError("ImageMagick is needed to build the frame standees.")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    missing: list[str] = []
    failed: list[str] = []
    wanted: set[str] = set()
    for name, spec in sorted(load_frames().items()):
        art = source / spec.image if spec.image else None
        if art is None or not art.is_file():
            missing.append(name)
            continue
        target = out_dir / f"{slug(name)}.png"
        wanted.add(target.name)
        if target.is_file() and not force \
                and target.stat().st_mtime_ns >= art.stat().st_mtime_ns:
            skipped += 1
            continue
        argv = _convert_argv(tool) + [
            str(art), "-trim", "+repage",
            "-resize", f"{width}x{width}>", "-strip",
            "-colors", "96", "-define", "png:compression-level=9",
            f"PNG8:{target}",
        ]
        if _run(argv) and target.is_file():
            written += 1
        else:
            failed.append(name)
    removed = 0
    for path in _files(out_dir):
        if path.name not in wanted:
            path.unlink()
            removed += 1
    summary = _summary("frames", out_dir, written, skipped, removed, failed)
    summary["missingArt"] = missing
    if not quiet:
        _report(summary)
    return summary


def build_tile_icons(
    source: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    *,
    width: int = TILE_ICON_WIDTH,
    force: bool = False,
    quiet: bool = False,
) -> dict[str, object]:
    """Shrink the glyphs the terrain cards stamp in a marked tile's corner."""
    source = Path(source) if source else TILE_ICON_SOURCE
    out_dir = Path(out_dir) if out_dir else TILE_DIR
    tool = _imagemagick()
    if tool is None:
        raise RuntimeError("ImageMagick is needed to build the tile glyphs.")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    failed: list[str] = []
    wanted: set[str] = set()
    for stem in TILE_ICON_FILES:
        art = source / f"{stem}.png"
        if not art.is_file():
            failed.append(stem)
            continue
        target = out_dir / f"{stem}.png"
        wanted.add(target.name)
        if target.is_file() and not force \
                and target.stat().st_mtime_ns >= art.stat().st_mtime_ns:
            skipped += 1
            continue
        argv = _convert_argv(tool) + [
            str(art), "-resize", f"{width}x{width}>", "-strip",
            "-colors", "32", "-define", "png:compression-level=9",
            f"PNG8:{target}",
        ]
        if _run(argv) and target.is_file():
            written += 1
        else:
            failed.append(stem)
    removed = 0
    for path in _files(out_dir):
        if path.name not in wanted:
            path.unlink()
            removed += 1
    summary = _summary("tiles", out_dir, written, skipped, removed, failed)
    if not quiet:
        _report(summary)
    return summary


def tile_icon_files() -> list[str]:
    return sorted(p.stem for p in _files(TILE_DIR))


def _summary(
    what: str, out_dir: Path, written: int, skipped: int,
    removed: int, failed: Iterable[str],
) -> dict[str, object]:
    files = list(_files(out_dir))
    return {
        "what": what,
        "dir": str(out_dir),
        "written": written,
        "skipped": skipped,
        "removed": removed,
        "failed": list(failed),
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
    }


def _report(summary: dict[str, object]) -> None:   # pragma: no cover - CLI
    print(
        f"{summary['what']}: {summary['files']} files, "
        f"{int(summary['bytes']) / 1_000_000:.2f} MB "
        f"({summary['written']} written, {summary['skipped']} up to date, "
        f"{summary['removed']} removed)"
    )
    if summary.get("failed"):
        print(f"  failed: {', '.join(summary['failed'])}")   # type: ignore[arg-type]
    if summary.get("missingArt"):
        print(f"  no art: {', '.join(summary['missingArt'])}")  # type: ignore[arg-type]


def main(argv: Optional[list[str]] = None) -> int:   # pragma: no cover - CLI
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m playtest.server.assets",
        description="Build the bundled terrain, token, frame and tile art for the board.",
    )
    parser.add_argument(
        "--what", choices=("all", "terrain", "tokens", "frames", "tiles"),
        default="all")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--terrain-width", type=int, default=TERRAIN_WIDTH)
    parser.add_argument("--token-width", type=int, default=TOKEN_WIDTH)
    parser.add_argument("--frame-width", type=int, default=FRAME_WIDTH)
    args = parser.parse_args(argv)
    try:
        if args.what in ("all", "terrain"):
            build_terrain(width=args.terrain_width, force=args.force)
        if args.what in ("all", "tokens"):
            build_tokens(width=args.token_width, force=args.force)
        if args.what in ("all", "frames"):
            build_frames(width=args.frame_width, force=args.force)
        if args.what in ("all", "tiles"):
            build_tile_icons(force=args.force)
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":                          # pragma: no cover
    raise SystemExit(main())
