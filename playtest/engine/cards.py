"""Card and deck loading, plus deck-legality validation.

Reads the repo-root action CSVs into the frozen `Card` contract from
`types.py`, reads `Frames.csv` into `FrameSpec`, loads `decks/*.csv` and
validates them against the deck-construction rules (rules.tex:812).

CSV quirks this module absorbs (see `simulation/simulate.py` for the same
set of traps in a different shape):

* `Initiative` may be a comma-separated list -- ``Quick Step`` is ``"8,3"``.
* `Movement` may carry an explicit ``+`` sign.
* Blank numeric cells mean 0; ``Pilot actions.csv`` has no attack/block
  columns at all.
* Every pilot card carries an implicit **High block** that is not printed
  in the CSV.
* Keywords live in the `Text` column as LaTeX macros; both ``\\kw`` and
  ``\\fullkw`` contain the bare keyword, so a lowercase substring test
  catches either form.
* `Persistence` is ``0`` (none), an integer turn count, or ``\\infty``.
* `PrintID` of ``0`` means the row is not part of the game.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from .types import Card, CardType, FrameSpec, ZONES, Zone

# --------------------------------------------------------------------------
# Locations
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DECK_DIR = REPO_ROOT / "decks"

#: source CSV -> card type. `Basic actions.csv` holds two types; rows whose
#: Group is "Frame" are faction frame cards, everything else is a basic.
CARD_CSVS: Mapping[str, CardType] = {
    "Weapon actions.csv": "weapon",
    "Basic actions.csv": "basic",
    "Booster actions.csv": "booster",
    "Pilot actions.csv": "pilot",
    "Drone actions.csv": "drone",
}

FRAMES_CSV = "Frames.csv"

#: Bare keyword -> the substrings that indicate it in a lowercased Text cell.
_KEYWORD_MARKERS: Mapping[str, tuple[str, ...]] = {
    "feint": ("feint",),
    "guardbreak": ("guardbreak", "guard break"),
    "committed": ("committed",),
    "closequarters": ("closequarters", "close quarters"),
    "knockback": ("knockback",),
    "reload": ("reload",),
    "onhit": ("onhit", "on hit"),
    "flying": ("flying",),
    "shield": ("shield",),
    "deathstrike": ("deathstrike",),
}

_KNOCKBACK_RE = re.compile(r"knockback[^{(\d]*[{(]?\s*(\d+)")
_SHIELD_RE = re.compile(r"shield\s*[({]?\s*(\d+)")


# --------------------------------------------------------------------------
# Cell parsing
# --------------------------------------------------------------------------


def _to_int(cell: object, default: int = 0) -> int:
    """Parse a CSV cell that should be an integer. Blank -> `default`."""
    if cell is None:
        return default
    text = str(cell).strip().replace("+", "")
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return default


def parse_initiative(cell: object) -> tuple[int, ...]:
    """`"8,3"` -> `(8, 3)`. A card acts once at each value it still has."""
    text = (str(cell) if cell is not None else "").strip().strip('"')
    if not text:
        return (0,)
    parts = [p.strip() for p in text.split(",") if p.strip()]
    values = tuple(_to_int(p) for p in parts)
    return values or (0,)


def parse_persistence(cell: object) -> Optional[int]:
    """`0`/blank -> 0 (none); `\\infty` -> None (permanent); else turn count."""
    text = (str(cell) if cell is not None else "").strip()
    if not text:
        return 0
    if "infty" in text or "∞" in text:
        return None
    return _to_int(text)


def parse_keywords(text: str) -> frozenset[str]:
    lowered = (text or "").lower()
    return frozenset(
        kw for kw, markers in _KEYWORD_MARKERS.items()
        if any(m in lowered for m in markers)
    )


def parse_knockback(text: str) -> int:
    match = _KNOCKBACK_RE.search((text or "").lower())
    return int(match.group(1)) if match else 0


def _zone_map(row: Mapping[str, str], suffix: str) -> dict[Zone, int]:
    return {z: _to_int(row.get(f"{z}{suffix}")) for z in ZONES}


def _dtype_map(row: Mapping[str, str]) -> dict[Zone, Optional[str]]:
    out: dict[Zone, Optional[str]] = {}
    for z in ZONES:
        raw = (row.get(f"{z}DType") or "").strip().lower()
        out[z] = raw or None
    return out


# --------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------


def card_from_row(row: Mapping[str, str], card_type: CardType) -> Optional[Card]:
    """Build one `Card`, or None if the row is blank or `PrintID == 0`."""
    name = (row.get("Name") or "").strip()
    if not name:
        return None
    if _to_int(row.get("PrintID"), default=1) == 0:
        return None

    group = (row.get("Group") or "").strip()
    # `Basic actions.csv` carries the faction frame cards under Group "Frame".
    if card_type == "basic" and group.lower() == "frame":
        card_type = "frame"

    blocks = _zone_map(row, "Block")
    if card_type == "pilot":
        # Not printed in the CSV: every pilot card blocks High (an ordinary
        # block, spent when used -- not a super block).
        blocks["High"] = max(blocks["High"], 1)

    text = (row.get("Text") or "").strip()
    return Card(
        key=f"{group}_{name}",
        name=name,
        group=group,
        faction=(row.get("Faction") or "").strip(),
        card_type=card_type,
        initiative=parse_initiative(row.get("Initiative")),
        movement=_to_int(row.get("Movement")),
        attacks=_zone_map(row, "Attack"),
        ranges=_zone_map(row, "Range"),
        dtypes=_dtype_map(row),
        blocks=blocks,
        text=text,
        keywords=parse_keywords(text),
        knockback=parse_knockback(text),
        persistence=parse_persistence(row.get("Persistence")),
        drone_health=_to_int(row.get("Drone_Health")),
        drone_movement=_to_int(row.get("Drone_MV")),
        image=(row.get("CardImg") or "").strip(),
    )


def load_cards(root: Optional[Path] = None) -> dict[str, Card]:
    """The whole catalogue, keyed by `"{Group}_{Name}"`."""
    base = Path(root) if root is not None else REPO_ROOT
    catalogue: dict[str, Card] = {}
    for filename, card_type in CARD_CSVS.items():
        path = base / filename
        if not path.exists():
            continue
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                card = card_from_row(row, card_type)
                if card is not None:
                    catalogue[card.key] = card
    return catalogue


def load_frames(root: Optional[Path] = None) -> dict[str, FrameSpec]:
    """Frame stats from `Frames.csv`, keyed by frame name."""
    base = Path(root) if root is not None else REPO_ROOT
    path = base / FRAMES_CSV
    frames: dict[str, FrameSpec] = {}
    if not path.exists():
        return frames
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("Name") or "").strip()
            if not name or _to_int(row.get("PrintID"), default=1) == 0:
                continue
            ability = (row.get("Abilities") or "").strip()
            lowered = ability.lower()
            keywords = frozenset(
                kw for kw in ("flying", "shield", "deathstrike") if kw in lowered
            )
            shield_match = _SHIELD_RE.search(lowered)
            frames[name] = FrameSpec(
                name=name,
                faction=(row.get("Faction") or "").strip(),
                movement=_to_int(row.get("Movement")),
                weapon_slots=_to_int(row.get("Weapon Slots")),
                booster_slots=_to_int(row.get("Boosters")),
                deck_size=_to_int(row.get("Deck size")),
                armour={
                    "High": _to_int(row.get("Top armour")),
                    "Mid": _to_int(row.get("Side armour")),
                    "Low": _to_int(row.get("Low armour")),
                },
                ability_text=ability,
                keywords=keywords,
                shield=int(shield_match.group(1)) if shield_match else 0,
                image=(row.get("CardImg") or "").strip(),
            )
    return frames


# --------------------------------------------------------------------------
# Decks
# --------------------------------------------------------------------------


def deck_path(name: str, deck_dir: Optional[Path] = None) -> Path:
    """Resolve a deck name (`"aegis_percival"`, `"deck_aegis_percival"`,
    `"deck_aegis_percival.csv"` or a full path) to a file."""
    base = Path(deck_dir) if deck_dir is not None else DECK_DIR
    candidate = Path(name)
    if candidate.is_absolute() or candidate.exists():
        return candidate
    stem = candidate.name
    for guess in (stem, f"{stem}.csv", f"deck_{stem}", f"deck_{stem}.csv"):
        path = base / guess
        if path.exists():
            return path
    return base / stem


def read_deck_keys(path: Path) -> list[str]:
    """One bare `Group_Name` per line; blanks and `#` comments skipped."""
    keys: list[str] = []
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue
            keys.append(entry[5:] if entry.startswith("card/") else entry)
    return keys


def load_deck(
    name: str,
    catalogue: Mapping[str, Card],
    deck_dir: Optional[Path] = None,
) -> list[Card]:
    """Load a deck CSV into `Card`s, in file order. Unknown keys raise."""
    path = deck_path(name, deck_dir)
    if not path.exists():
        raise FileNotFoundError(f"no such deck: {name}")
    deck: list[Card] = []
    missing: list[str] = []
    for key in read_deck_keys(path):
        card = catalogue.get(key)
        if card is None:
            missing.append(key)
        else:
            deck.append(card)
    if missing:
        raise ValueError(f"{path.name}: unknown cards {missing}")
    return deck


#: Deck files under `decks/` that are not frame decks. Terrain and objective
#: decks are the battlefield's, loaded by `setup.py`, not by `load_deck`.
NON_FRAME_DECK_PREFIXES = ("deck_terrain", "deck_objective")


def available_decks(deck_dir: Optional[Path] = None) -> list[str]:
    """Frame-deck names (not terrain or objective decks, not the frames list)."""
    base = Path(deck_dir) if deck_dir is not None else DECK_DIR
    if not base.exists():
        return []
    return sorted(
        p.stem for p in base.glob("deck_*.csv")
        if not p.stem.startswith(NON_FRAME_DECK_PREFIXES)
    )


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def frame_for_deck(
    deck_name: str, frames: Mapping[str, FrameSpec]
) -> Optional[FrameSpec]:
    """Guess a deck's frame from its filename (`deck_<faction>_<frame>`).

    The shipped decks name the frame loosely -- `deck_revolution_ripper` is
    `RipperSmasher`, `deck_guild_salaryman` is `J7R-Salaryman` -- so the last
    filename token is matched as a substring of the normalised frame name.
    """
    stem = Path(deck_name).stem
    if stem.startswith("deck_"):
        stem = stem[len("deck_"):]
    token = _normalise(stem.split("_")[-1])
    if not token:
        return None
    best: Optional[FrameSpec] = None
    for spec in frames.values():
        norm = _normalise(spec.name)
        if token and token in norm:
            if best is None or len(norm) < len(_normalise(best.name)):
                best = spec
    return best


# --------------------------------------------------------------------------
# Deck legality (rules.tex:812)
# --------------------------------------------------------------------------

MAX_PILOT_CARDS = 4


@dataclass(frozen=True)
class DeckReport:
    """The outcome of validating one deck against one frame."""

    deck: str
    frame: Optional[str]
    size: int
    errors: tuple[str, ...] = ()

    @property
    def legal(self) -> bool:
        return not self.errors


def weapon_slots_used(deck: Iterable[Card]) -> dict[str, int]:
    """Slots each weapon group consumes.

    "Each weapon is a set of up to 4 attack cards. For each weapon slot you
    can include up to 1 of each card in that weapon" -- so a group needs as
    many slots as the most-duplicated card in it. Drones take a weapon slot.
    """
    per_group: dict[str, Counter] = {}
    for card in deck:
        if card.card_type in ("weapon", "drone"):
            per_group.setdefault(card.group, Counter())[card.key] += 1
    return {group: max(counts.values()) for group, counts in per_group.items()}


def _frame_lock(card: Card) -> Optional[str]:
    """`"Can only be used by X"` in card text, if present."""
    match = re.search(r"can only be used by\s+([^\\\n]+)", card.text, re.I)
    return match.group(1).strip() if match else None


def validate_deck(
    deck: Sequence[Card],
    frame: Optional[FrameSpec],
    *,
    deck_name: str = "",
) -> DeckReport:
    """Check one deck against the deck-construction rules.

    Returns every violation found rather than stopping at the first.
    """
    errors: list[str] = []
    if frame is None:
        return DeckReport(deck_name, None, len(deck), ("no frame identified for deck",))

    # -- exact deck size -------------------------------------------------
    if len(deck) != frame.deck_size:
        errors.append(
            f"deck size {len(deck)} != {frame.name}'s deck size {frame.deck_size}"
        )

    # -- pilot cards -----------------------------------------------------
    pilots = [c for c in deck if c.card_type == "pilot"]
    if len(pilots) > MAX_PILOT_CARDS:
        errors.append(f"{len(pilots)} pilot cards (max {MAX_PILOT_CARDS})")
    pilot_groups = sorted({c.group for c in pilots})
    if len(pilot_groups) > 1:
        errors.append(f"pilot cards from several pilots: {pilot_groups}")
    dupe_pilots = [k for k, n in Counter(c.key for c in pilots).items() if n > 1]
    if dupe_pilots:
        errors.append(f"duplicate pilot cards: {sorted(dupe_pilots)}")

    # -- boosters --------------------------------------------------------
    boosters = [c for c in deck if c.card_type == "booster"]
    if len(boosters) > frame.booster_slots:
        errors.append(
            f"{len(boosters)} booster cards (max {frame.booster_slots})"
        )

    # -- weapons ---------------------------------------------------------
    slots = weapon_slots_used(deck)
    used = sum(slots.values())
    if used > frame.weapon_slots:
        detail = ", ".join(f"{g}x{n}" for g, n in sorted(slots.items()))
        errors.append(
            f"{used} weapon slots used (max {frame.weapon_slots}): {detail}"
        )

    # -- frame cards -----------------------------------------------------
    frame_cards = [c for c in deck if c.card_type == "frame"]
    dupe_frames = [k for k, n in Counter(c.key for c in frame_cards).items() if n > 1]
    if dupe_frames:
        errors.append(f"duplicate frame cards: {sorted(dupe_frames)}")

    # -- faction lock ----------------------------------------------------
    for card in sorted({c.key: c for c in deck}.values(), key=lambda c: c.key):
        if card.faction and card.faction != frame.faction:
            errors.append(
                f"{card.key} is {card.faction}-only, frame is {frame.faction}"
            )
        lock = _frame_lock(card)
        if lock and _normalise(lock) != _normalise(frame.name):
            # The CSV misspells "J7R-Salryman"; accept a near match.
            import difflib

            ratio = difflib.SequenceMatcher(
                None, _normalise(lock), _normalise(frame.name)
            ).ratio()
            if ratio < 0.85:
                errors.append(f"{card.key} can only be used by {lock}")

    return DeckReport(deck_name or "", frame.name, len(deck), tuple(errors))


def validate_all_decks(
    root: Optional[Path] = None, deck_dir: Optional[Path] = None
) -> list[DeckReport]:
    """Validate every shipped `decks/deck_*.csv`."""
    catalogue = load_cards(root)
    frames = load_frames(root)
    reports: list[DeckReport] = []
    for name in available_decks(deck_dir):
        try:
            deck = load_deck(name, catalogue, deck_dir)
        except (ValueError, FileNotFoundError) as exc:
            reports.append(DeckReport(name, None, 0, (str(exc),)))
            continue
        spec = frame_for_deck(name, frames)
        reports.append(validate_deck(deck, spec, deck_name=name))
    return reports


if __name__ == "__main__":  # pragma: no cover - operator convenience
    for report in validate_all_decks():
        status = "OK  " if report.legal else "FAIL"
        print(f"{status} {report.deck} ({report.frame}, {report.size} cards)")
        for err in report.errors:
            print(f"       - {err}")
