"""Card art, sized for a phone screen and carried with the app.

The app runs offline on the phone, so the art has to be on the phone. What
workstream E renders into `AllCardImages/` is 158 PNGs at 378x537 print
density, about 90 kB each -- 24 MB of files whose pixels are four times larger
than any phone will ever draw them.

So there are two places art can come from:

1. **The bundle** (`playtest/server/static/cards/*.jpg`) -- pre-sized JPEGs,
   about 12 kB each, roughly 2 MB for the whole set. This is what ships and
   what the phone reads. Build it on a desktop with

       python -m playtest.server.images

2. **The originals** (`AllCardImages/`) -- used when the bundle has no entry,
   downscaled on demand if ImageMagick is present. This is the desktop
   development path; the phone is not expected to have either.

Resolution is by card `key` (`"{Group}_{Name}"`), which is also the filename
stem. Those filenames contain spaces, so a client must URL-encode them.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]

#: What workstream E renders. `NETFRAME_CARD_IMAGES` overrides it.
IMAGE_DIR = Path(os.environ.get("NETFRAME_CARD_IMAGES") or (REPO_ROOT / "AllCardImages"))

#: The pre-sized art that ships with the app. `NETFRAME_CARD_THUMBS` overrides.
BUNDLE_DIR = Path(
    os.environ.get("NETFRAME_CARD_THUMBS")
    or (Path(__file__).resolve().parent / "static" / "cards")
)

#: On-demand resizes are cached outside the repo so nothing generated lands in
#: git. Only used when the bundle has no entry for a card.
CACHE_DIR = Path(
    os.environ.get("NETFRAME_THUMB_CACHE")
    or (Path(tempfile.gettempdir()) / "netframe-card-thumbs")
)

#: Widths the client may ask for, so a hostile query cannot fill the disk.
ALLOWED_WIDTHS = (120, 180, 240, 360, 540)

#: 240 px covers a card drawn full-width on a 3x phone's planning screen and
#: is four cards to a row at 2x. Bundling one size keeps the bundle small.
DEFAULT_WIDTH = 240

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

_INDEX: Optional[dict[str, Path]] = None


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _files(directory: Path) -> Iterable[Path]:
    if not directory.is_dir():
        return ()
    return (p for p in sorted(directory.iterdir())
            if p.suffix.lower() in IMAGE_SUFFIXES)


def _index() -> dict[str, Path]:
    """Normalised stem -> file, so `Percival MkIV` finds `Percival_MkIV.png`.

    The bundle wins over the originals: it is what the phone has.
    """
    global _INDEX
    if _INDEX is None:
        index: dict[str, Path] = {}
        for directory in (IMAGE_DIR, BUNDLE_DIR):      # bundle registered last
            for path in _files(directory):
                index[_normalise(path.stem)] = path
        _INDEX = index
    return _INDEX


def refresh() -> None:
    """Forget the filename index (art was rendered or the bundle was built)."""
    global _INDEX
    _INDEX = None


def available() -> list[str]:
    """Every card key art exists for, in the client's own key shape."""
    keys = {p.stem for p in _files(BUNDLE_DIR)} | {p.stem for p in _files(IMAGE_DIR)}
    return sorted(keys)


def find(key: str) -> Optional[Path]:
    """Resolve a card key (or frame/terrain name) to a file on disk."""
    key = (key or "").strip()
    if not key:
        return None
    if "/" in key or "\\" in key or ".." in key:
        return None
    for directory in (BUNDLE_DIR, IMAGE_DIR):
        for candidate in (key, key.replace(" ", "_"), key.replace("_", " ")):
            for suffix in IMAGE_SUFFIXES:
                path = directory / f"{candidate}{suffix}"
                if path.is_file():
                    return path
    return _index().get(_normalise(key))


def is_bundled(path: Path) -> bool:
    try:
        path.resolve().relative_to(BUNDLE_DIR.resolve())
        return True
    except (ValueError, OSError):
        return False


def _imagemagick() -> Optional[str]:
    for name in ("magick", "convert"):
        found = shutil.which(name)
        if found:
            return found
    return None


def thumbnail(path: Path, width: int = DEFAULT_WIDTH) -> tuple[bytes, str]:
    """`(bytes, content-type)` for a copy sized for the screen.

    A bundled file is already the right size, so it is returned untouched --
    which is the whole point on a phone with no ImageMagick.
    """
    if is_bundled(path):
        return path.read_bytes(), _content_type(path)
    width = width if width in ALLOWED_WIDTHS else DEFAULT_WIDTH
    tool = _imagemagick()
    if tool is None:
        return path.read_bytes(), _content_type(path)
    stat = path.stat()
    stamp = hashlib.sha1(
        f"{path}|{stat.st_mtime_ns}|{stat.st_size}|{width}".encode()
    ).hexdigest()[:20]
    cached = CACHE_DIR / f"{stamp}.jpg"
    if cached.is_file():
        return cached.read_bytes(), "image/jpeg"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not _resize(tool, path, cached, width):
        return path.read_bytes(), _content_type(path)
    return cached.read_bytes(), "image/jpeg"


def _resize(tool: str, source: Path, target: Path, width: int) -> bool:
    argv = [tool]
    if Path(tool).name == "magick":
        argv.append("convert")
    argv += [
        str(source), "-resize", f"{width}x", "-strip",
        "-background", "white", "-flatten",
        "-interlace", "Plane", "-quality", "82", str(target),
    ]
    try:
        subprocess.run(argv, check=True, capture_output=True, timeout=60)
    except Exception:
        return False
    return target.is_file()


def _content_type(path: Path) -> str:
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/png")


# --------------------------------------------------------------------------
# Building the bundle
# --------------------------------------------------------------------------


def prebuild(
    width: int = DEFAULT_WIDTH,
    source: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    *,
    force: bool = False,
    quiet: bool = False,
) -> dict[str, object]:
    """Render every original into the bundle at display size.

    Run this on a desktop (it needs ImageMagick) before copying the repo to the
    phone. Returns a small summary so a caller can print or assert on it.
    """
    source = Path(source) if source else IMAGE_DIR
    out_dir = Path(out_dir) if out_dir else BUNDLE_DIR
    tool = _imagemagick()
    if tool is None:
        raise RuntimeError(
            "ImageMagick (`convert` or `magick`) is needed to build the card "
            "bundle. Build it on a desktop, then copy the repo to the phone."
        )
    originals = list(_files(source))
    if not originals:
        raise FileNotFoundError(f"no card art in {source}")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = 0
    failed: list[str] = []
    for path in originals:
        target = out_dir / f"{path.stem}.jpg"
        if target.is_file() and not force \
                and target.stat().st_mtime_ns >= path.stat().st_mtime_ns:
            skipped += 1
            continue
        if _resize(tool, path, target, width):
            written += 1
        else:
            failed.append(path.name)
    # Anything in the bundle whose original is gone is stale.
    stems = {p.stem for p in originals}
    removed = 0
    for path in _files(out_dir):
        if path.stem not in stems:
            path.unlink()
            removed += 1
    refresh()
    total = sum(p.stat().st_size for p in _files(out_dir))
    summary = {
        "source": str(source),
        "bundle": str(out_dir),
        "width": width,
        "written": written,
        "skipped": skipped,
        "removed": removed,
        "failed": failed,
        "files": len(list(_files(out_dir))),
        "bytes": total,
    }
    if not quiet:
        print(
            f"card bundle: {summary['files']} files, "
            f"{total / 1_000_000:.1f} MB at {width}px wide "
            f"({written} written, {skipped} up to date, {removed} removed)"
        )
        if failed:
            print(f"  failed: {', '.join(failed)}")
    return summary


def main(argv: Optional[list[str]] = None) -> int:      # pragma: no cover - CLI
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m playtest.server.images",
        description="Build the bundled, phone-sized card art.",
    )
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH,
                        help=f"pixel width (default {DEFAULT_WIDTH})")
    parser.add_argument("--source", type=Path, default=IMAGE_DIR)
    parser.add_argument("--out", type=Path, default=BUNDLE_DIR)
    parser.add_argument("--force", action="store_true",
                        help="rebuild even when up to date")
    args = parser.parse_args(argv)
    try:
        prebuild(args.width, args.source, args.out, force=args.force)
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":                              # pragma: no cover
    raise SystemExit(main())
