"""What build is this? -- the version marker the client shows.

A phone runs this app straight out of a git clone: there is no packaging step,
no version number anyone bumps, and no way to tell a screen full of cards you
pulled this morning from the one you pulled last week. So the marker is
computed from the code itself -- every Python module the app runs and every
static file the browser loads, hashed into a short id that is *the same on
every machine holding the same code* and different the moment any of it
changes.

`cards_id()` is the same idea for the **data**, and it is a separate number on
purpose. The card CSVs are edited far more often than the code and by a
different activity -- balancing -- so a single marker would move for both
reasons and tell you neither. A saved game carries both, and a game played
before a balance pass is not evidence about the cards as they stand now.

Two deliberate limits:

* **Code, markup and card data only.** Art is not hashed: it is megabytes, it
  changes on its own schedule, and a rebuilt terrain bundle is visible on the
  board anyway. `/api/health` already counts the bundles.
* **No subprocess, ever.** The commit, when the app is running from a clone,
  is read out of `.git` by hand. Termux may not have git on PATH, and a build
  marker that can fail to load is worse than no marker at all.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterator, Optional

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent

#: What counts as "the build": the code that runs and the files the browser
#: loads. Tests are excluded -- they are not shipped behaviour, and a marker
#: that moved every time a test moved would cry wolf.
SOURCE_DIRS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("engine", (".py",)),
    ("ai", (".py",)),
    ("server", (".py",)),
    ("server/static", (".html", ".css", ".js", ".mjs", ".webmanifest")),
)

#: The files that decide what the cards *do*. Everything `engine.cards` reads,
#: which is the whole of balance: stats, keywords, text, frame armour, and the
#: terrain that carries the objectives. Deck lists are deliberately absent --
#: a saved game names the decks it used, and an edit to a deck nobody played
#: does not make that game stale.
CARD_SOURCES: tuple[str, ...] = (
    "Weapon actions.csv",
    "Basic actions.csv",
    "Booster actions.csv",
    "Pilot actions.csv",
    "Drone actions.csv",
    "Frames.csv",
    "Terrain_square.csv",
    "Terrain_hex.csv",
)

_CACHE: dict[str, Any] = {}


def _files() -> Iterator[Path]:
    for relative, suffixes in SOURCE_DIRS:
        root = PACKAGE_ROOT / relative
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            if "__pycache__" in path.parts:
                continue
            yield path


def _stamp() -> tuple[tuple[str, int, int], ...]:
    """A cheap fingerprint of the tree, to know when to re-hash it.

    Static files are edited while the server is running (the browser refetches
    them -- they are served `no-cache`), so the marker cannot be computed once
    at import and left. Stat is cheap; hashing is what this avoids repeating.
    """
    out = []
    for path in _files():
        try:
            info = path.stat()
        except OSError:                        # pragma: no cover - racing edit
            continue
        out.append((str(path), info.st_size, info.st_mtime_ns))
    return tuple(out)


def build_id() -> str:
    """Eight hex characters over the content of every source file."""
    stamp = _stamp()
    if _CACHE.get("stamp") == stamp:
        return str(_CACHE["build"])
    digest = hashlib.sha256()
    count = 0
    for path in _files():
        try:
            body = path.read_bytes()
        except OSError:                        # pragma: no cover - racing edit
            continue
        digest.update(path.relative_to(PACKAGE_ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(body).hexdigest().encode())
        digest.update(b"\n")
        count += 1
    _CACHE.update(stamp=stamp, build=digest.hexdigest()[:8], files=count)
    return str(_CACHE["build"])


def cards_id() -> str:
    """Eight hex characters over the card data, ignoring the code entirely.

    Recomputed from mtimes like `build_id`, because a balance pass happens
    while the server is up and the point of the number is to notice.
    """
    stamp = []
    for name in CARD_SOURCES:
        path = REPO_ROOT / name
        try:
            info = path.stat()
        except OSError:
            continue
        stamp.append((name, info.st_size, info.st_mtime_ns))
    key = tuple(stamp)
    if _CACHE.get("cards_stamp") == key:
        return str(_CACHE["cards"])
    digest = hashlib.sha256()
    for name in CARD_SOURCES:
        path = REPO_ROOT / name
        try:
            body = path.read_bytes()
        except OSError:                        # a CSV that is simply not there
            continue
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(body).hexdigest().encode())
        digest.update(b"\n")
    _CACHE.update(cards_stamp=key, cards=digest.hexdigest()[:8])
    return str(_CACHE["cards"])


def commit() -> Optional[str]:
    """The checked-out commit, short, read straight out of `.git`."""
    git = REPO_ROOT / ".git"
    try:
        if git.is_file():                      # a worktree: ".git" is a pointer
            target = git.read_text(encoding="utf-8").split("gitdir:", 1)[1].strip()
            git = Path(target)
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head[:7] or None
        ref = head.split(":", 1)[1].strip()
        loose = git / ref
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip()[:7] or None
        packed = git / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(f" {ref}"):
                    return line.split()[0][:7]
    except Exception:
        return None
    return None


def info() -> dict[str, Any]:
    """`{build, cardData, commit, files}` -- what the client puts on screen.

    `cardData` and not `cards`: `/api/health` has meant "how many cards are in
    the catalogue" by that name since the first version of it, and a hash
    silently overwriting a count is exactly the sort of thing nobody notices
    until a log says something false.
    """
    build = build_id()
    return {
        "build": build,
        "cardData": cards_id(),
        "commit": commit(),
        "files": int(_CACHE.get("files", 0)),
    }
