#!/usr/bin/env python3
"""
Finds action cards that are strictly better than other action cards for the same
printed effect, and reports the group-level profile of every weapon group so the
per-card gaps can be read against how different the two groups otherwise are.

Two checks:

  within  For each group, sets of cards with the same effect but different cost.
          "Same effect" = identical text + persistence + the same attack and
          block values, ignoring which zone they sit in. Cost = initiative and
          movement. Flags which member of a set is strictly cheaper.

  cross   Every pair of cards from *different* groups where one dominates the
          other: >= on attack, block, range, initiative and movement, with at
          least one strict. Only cards printing identical text and persistence
          are compared, since text can be a cost as easily as a bonus.

Damage type is recorded and printed but never gates a comparison: it changes
which frame abilities apply, not the power of the card.

Zone placement is always treated as interchangeable: attack and block values are
compared as sorted shapes, so High 2 / Low 1 and Mid 2 / High 1 are the same
card. Which zone a card hits is a matchup difference, not a power difference.

Run:
  python weapon_dominance.py
  python weapon_dominance.py --group Mace --group Halberd
  python weapon_dominance.py --csv "Drone actions.csv" --markdown
"""

import argparse
import csv
import itertools
import os
import re
from collections import defaultdict

ZONES = ("High", "Mid", "Low")
ZONE_TAG = {"High": "H", "Mid": "M", "Low": "L"}

DEFAULT_CSV = "Weapon actions.csv"

# Extra columns that behave like effect stats (higher is better) when present.
EXTRA_STATS = ("Drone_Health", "Drone_MV")


def to_int(value):
    value = (value or "").strip()
    if not value:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def clean_text(value):
    """Normalise card text for equality: collapse whitespace, drop LaTeX line breaks."""
    value = (value or "").replace("\\\\", " ")
    return re.sub(r"\s+", " ", value).strip()


class Card:
    def __init__(self, row):
        self.row = row
        self.name = (row.get("Name") or "").strip()
        self.group = (row.get("Group") or "").strip()
        self.init = to_int(row.get("Initiative"))
        self.mv = to_int(row.get("Movement"))
        self.attack = {z: to_int(row.get(z + "Attack")) for z in ZONES}
        self.block = {z: to_int(row.get(z + "Block")) for z in ZONES}
        self.range = {z: to_int(row.get(z + "Range")) for z in ZONES}
        self.dtype = {z: (row.get(z + "DType") or "").strip() for z in ZONES}
        self.text = clean_text(row.get("Text"))
        self.persistence = (row.get("Persistence") or "").strip()
        self.extra = {k: to_int(row.get(k)) for k in EXTRA_STATS if k in row}

    # -- rendering -------------------------------------------------------
    def damage_types(self):
        return sorted({self.dtype[z] for z in ZONES if self.attack[z] and self.dtype[z]})

    def dtype_label(self):
        return "/".join(self.damage_types()) or "—"

    def zone_cells(self):
        cells = []
        for z in ZONES:
            parts = []
            if self.attack[z]:
                shot = f"{self.attack[z]} {self.dtype[z] or '?'}"
                if self.range[z]:
                    shot += f" @{self.range[z]}"
                parts.append(shot)
            if self.block[z]:
                parts.append(f"blk {self.block[z]}")
            cells.append(f"{ZONE_TAG[z]} " + (", ".join(parts) if parts else "—"))
        return cells

    def stat_line(self):
        extra = "".join(f"  {k.replace('Drone_', '')} {v}" for k, v in self.extra.items() if v)
        pers = f"  persist {self.persistence}" if self.persistence not in ("", "0") else ""
        return (f"init {self.init:>2}  mv {self.mv:>2}   "
                + " | ".join(f"{c:<20}" for c in self.zone_cells())
                + extra + pers)

    def full_label(self):
        return f"{self.group} {self.name}"


def load_cards(path, groups=None, include_unprinted=False):
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        missing = [z + "Attack" for z in ZONES if z + "Attack" not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"{path} has no attack zone columns ({', '.join(missing)}); "
                             "this check needs a CSV with the High/Mid/Low Attack+Block+DType+Range set.")
    cards = []
    for row in rows:
        if not include_unprinted and (row.get("PrintID") or "1").strip() == "0":
            continue
        card = Card(row)
        if not card.name:
            continue
        if groups and card.group not in groups:
            continue
        cards.append(card)
    return cards


# -- dominance ------------------------------------------------------------

def comparable(a, b):
    """Cards are only ranked against each other if they print the same effect text."""
    return a.text == b.text and a.persistence == b.persistence


def _shape(card, kind):
    """Sorted (value, range) pairs for a card's attacks or blocks, padded to 3 zones."""
    if kind == "attack":
        vals = sorted(((card.attack[z], card.range[z]) for z in ZONES if card.attack[z]),
                      key=lambda t: (-t[0], -t[1]))
    else:
        vals = sorted(((card.block[z], 0) for z in ZONES if card.block[z]),
                      key=lambda t: -t[0])
    return vals + [(0, 0)] * (len(ZONES) - len(vals))


def _deltas(a, b):
    """Every dimension on which a is measured against b. All >= 0 means a dominates."""
    deltas = []
    for kind in ("attack", "block"):
        for idx, ((av, ar), (bv, br)) in enumerate(zip(_shape(a, kind), _shape(b, kind)), 1):
            deltas.append((f"{kind} #{idx}", av - bv))
            if kind == "attack" and bv:
                deltas.append((f"range #{idx}", ar - br if av else -1))
    deltas.append(("initiative", a.init - b.init))
    deltas.append(("movement", a.mv - b.mv))
    for key in a.extra:
        deltas.append((key.replace("Drone_", ""), a.extra[key] - b.extra.get(key, 0)))
    return deltas


def dominates(a, b):
    deltas = _deltas(a, b)
    if any(d < 0 for _, d in deltas):
        return None
    wins = [(n, d) for n, d in deltas if d > 0]
    return wins or None


WIN_ORDER = ("init", "mv", "atk", "blk", "rng", "Health", "MV")


def base_dimension(name):
    """Collapse 'H attack' / 'attack #2' to the category the reader cares about."""
    for token, base in (("initiative", "init"), ("movement", "mv"), ("attack", "atk"),
                        ("block", "blk"), ("range", "rng")):
        if token in name:
            return base
    return name


def describe_wins(wins):
    """Sum the deltas per category: '+4 init, +1 mv' rather than a per-zone breakdown."""
    totals = defaultdict(int)
    for name, delta in wins:
        totals[base_dimension(name)] += delta
    ordered = sorted(totals.items(),
                     key=lambda kv: WIN_ORDER.index(kv[0]) if kv[0] in WIN_ORDER else len(WIN_ORDER))
    return ", ".join(f"+{d} {n}" for n, d in ordered if d)


# -- checks ---------------------------------------------------------------

def effect_key(card):
    shape = (tuple(_shape(card, "attack")), tuple(_shape(card, "block")))
    return shape, card.text, card.persistence, tuple(sorted(card.extra.items()))


def check_within(cards):
    """Same group, same effect, different cost."""
    by_group = defaultdict(lambda: defaultdict(list))
    for card in cards:
        by_group[card.group][effect_key(card)].append(card)

    results = []
    for group in sorted(by_group):
        for members in by_group[group].values():
            costs = {(c.init, c.mv) for c in members}
            if len(costs) < 2:
                continue
            ranked = sorted(members, key=lambda c: (-c.init, -c.mv, c.name))
            beaten = []
            for a, b in itertools.permutations(ranked, 2):
                if (a.init >= b.init and a.mv >= b.mv
                        and (a.init, a.mv) != (b.init, b.mv)):
                    beaten.append((a, b))
            results.append((group, ranked, beaten))
    return results


def check_cross(cards):
    """Cross-group dominance, clustered so each card and each group appears once.

    A card dominated by three others, or dominating three others, is one
    interaction to look at rather than three findings, so edges are grouped into
    connected components over the cards they touch.
    """
    edges = []
    for a, b in itertools.permutations(cards, 2):
        if a.group == b.group or not comparable(a, b):
            continue
        wins = dominates(a, b)
        if wins:
            edges.append((a, b, wins))
    if not edges:
        return []

    # Connected components over every card an edge touches.
    parent = {}

    def find(card):
        parent.setdefault(id(card), id(card))
        root = id(card)
        while parent[root] != root:
            parent[root] = parent[parent[root]]
            root = parent[root]
        return root

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for a, b, _ in edges:
        union(a, b)

    members = defaultdict(dict)
    grouped_edges = defaultdict(list)
    for a, b, wins in edges:
        root = find(a)
        members[root][id(a)] = a
        members[root][id(b)] = b
        grouped_edges[root].append((a, b, wins))

    clusters = []
    for root, by_id in members.items():
        cluster_cards = list(by_id.values())
        cluster_edges = grouped_edges[root]
        beats = defaultdict(list)   # loser id -> [(winner, wins), ...]
        wins_count = defaultdict(int)
        for a, b, w in cluster_edges:
            beats[id(b)].append((a, w))
            wins_count[id(a)] += 1
        # Winners first, then by initiative, so the strongest card heads the block.
        cluster_cards.sort(key=lambda c: (-wins_count[id(c)], len(beats[id(c)]), -c.init, c.group))
        for loser in beats.values():
            loser.sort(key=lambda t: -sum(d for _, d in t[1]))
        widest = max(sum(d for _, d in w) for _, _, w in cluster_edges)
        clusters.append({
            "cards": cluster_cards,
            "edges": cluster_edges,
            "beats": beats,
            "wins_count": wins_count,
            "widest": widest,
            "text": cluster_cards[0].text,
            "groups": sorted({c.group for c in cluster_cards}),
        })
    clusters.sort(key=lambda c: (-c["widest"], -len(c["edges"]), c["cards"][0].name))
    return clusters


def group_profiles(cards):
    by_group = defaultdict(list)
    for card in cards:
        by_group[card.group].append(card)

    profiles = []
    for group in sorted(by_group):
        members = by_group[group]
        atk_zones = "".join(ZONE_TAG[z] if any(c.attack[z] for c in members) else "·" for z in ZONES)
        blk_zones = "".join(ZONE_TAG[z] if any(c.block[z] for c in members) else "·" for z in ZONES)
        ranges = [c.range[z] for c in members for z in ZONES if c.attack[z]]
        inits = [c.init for c in members]
        moves = [c.mv for c in members]
        dtypes = sorted({c.dtype[z] for c in members for z in ZONES if c.attack[z] and c.dtype[z]})
        profiles.append({
            "group": group,
            "n": len(members),
            "atk_zones": atk_zones,
            "blk_zones": blk_zones,
            "range": f"{min(ranges)}-{max(ranges)}" if ranges and min(ranges) != max(ranges)
                     else (str(ranges[0]) if ranges else "—"),
            "init": f"{min(inits)}-{max(inits)}",
            "init_avg": sum(inits) / len(inits),
            "mv": f"{min(moves)}..{max(moves)}",
            "atk_per_card": sum(c.attack[z] for c in members for z in ZONES) / len(members),
            "blk_per_card": sum(c.block[z] for c in members for z in ZONES) / len(members),
            "dtypes": "/".join(dtypes) or "—",
        })
    return {p["group"]: p for p in profiles}, profiles


# -- output ---------------------------------------------------------------

def cluster_dtypes(cluster):
    """Name the damage types actually present, so a cross-type gap is visible up front."""
    types = sorted({t for card in cluster["cards"] for t in card.damage_types()})
    if not types:
        return "block only"
    return ("all " if len(types) == 1 else "") + ", ".join(types)


def profile_row_text(p):
    return (f"{p['group']:<15}{p['n']:>2}  {p['atk_zones']:<5}{p['blk_zones']:<5}"
            f"{p['range']:<8}{p['init']:<6}{p['init_avg']:<6.1f}{p['mv']:<8}"
            f"{p['atk_per_card']:<6.2f}{p['blk_per_card']:<6.2f}{p['dtypes']}")


def report_text(within, cross, profiles_by_group, profiles, show):
    out = []
    if "within" in show:
        out.append("=" * 78)
        out.append("SAME GROUP, SAME EFFECT, DIFFERENT COST")
        out.append("=" * 78)
        if not within:
            out.append("  none")
        for group, ranked, beaten in within:
            out.append(f"\n{group}   text: {ranked[0].text or '(none)'}   dmg: {ranked[0].dtype_label()}")
            losers = {id(b) for _, b in beaten}
            for card in ranked:
                mark = "  <- strictly worse" if id(card) in losers else ""
                out.append(f"    {card.name:<22}{card.stat_line()}{mark}")
            for a, b in beaten:
                out.append(f"      {a.name} beats {b.name}: {describe_wins(_positive(a, b))}")

    if "cross" in show:
        out.append("")
        out.append("=" * 78)
        out.append("CROSS-GROUP DOMINANCE")
        out.append("=" * 78)
        if not cross:
            out.append("  none")
        for idx, cluster in enumerate(cross, 1):
            out.append(f"\n{idx}. {' / '.join(cluster['groups'])}   "
                       f"({len(cluster['cards'])} comparable cards, {cluster_dtypes(cluster)})")
            out.append(f"     text: {cluster['text'] or '(none)'}")
            for card in cluster["cards"]:
                tag = "  <- dominated" if cluster["beats"].get(id(card)) else ""
                out.append(f"     {card.full_label():<28}{card.stat_line()}{tag}")
            for card in cluster["cards"]:
                losses = cluster["beats"].get(id(card))
                if losses:
                    beaten_by = "; ".join(f"{w.full_label()} ({describe_wins(ws)})" for w, ws in losses)
                    out.append(f"       {card.full_label()} loses to {beaten_by}")
            for group in cluster["groups"]:
                p = profiles_by_group.get(group)
                if p:
                    out.append(f"       group {p['group']:<15} atk {p['atk_zones']}  blk {p['blk_zones']}  "
                               f"range {p['range']:<7} init {p['init']} (av {p['init_avg']:.1f})  "
                               f"mv {p['mv']:<7} atk/card {p['atk_per_card']:.2f}  blk/card {p['blk_per_card']:.2f}")

    if "profiles" in show:
        out.append("")
        out.append("=" * 78)
        out.append("GROUP PROFILES")
        out.append("=" * 78)
        out.append(f"{'Group':<15}{'n':>2}  {'atkZ':<5}{'blkZ':<5}{'range':<8}{'init':<6}{'av':<6}"
                   f"{'mv':<8}{'atk/c':<6}{'blk/c':<6}dmg types")
        for p in profiles:
            out.append(profile_row_text(p))
    return "\n".join(out)


def _positive(a, b):
    wins = []
    if a.init > b.init:
        wins.append(("initiative", a.init - b.init))
    if a.mv > b.mv:
        wins.append(("movement", a.mv - b.mv))
    return wins


def report_markdown(within, cross, profiles_by_group, profiles, show):
    out = []
    if "within" in show:
        out.append("## Same group, same effect, different cost\n")
        if not within:
            out.append("None.\n")
        for group, ranked, beaten in within:
            out.append(f"**{group}** — {ranked[0].text or 'no text'} · {ranked[0].dtype_label()}\n")
            out.append("| Card | Init | Mv | High | Mid | Low |")
            out.append("|---|---|---|---|---|---|")
            for card in ranked:
                cells = " | ".join(c[2:] for c in card.zone_cells())
                out.append(f"| {card.name} | {card.init} | {card.mv} | {cells} |")
            for a, b in beaten:
                out.append(f"\n*{a.name} strictly beats {b.name}: {describe_wins(_positive(a, b))}.*")
            out.append("")

    if "cross" in show:
        out.append("## Cross-group dominance\n")
        if not cross:
            out.append("None.\n")
        for idx, cluster in enumerate(cross, 1):
            out.append(f"### {idx}. {' / '.join(cluster['groups'])} — "
                       f"{len(cluster['cards'])} comparable cards, {cluster_dtypes(cluster)}\n")
            out.append(f"Text: {cluster['text'] or 'none'}.\n")
            out.append("| Card | Init | Mv | High | Mid | Low | Dmg | |")
            out.append("|---|---|---|---|---|---|---|---|")
            for card in cluster["cards"]:
                cells = " | ".join(c[2:] for c in card.zone_cells())
                tag = "**dominated**" if cluster["beats"].get(id(card)) else ""
                out.append(f"| {card.full_label()} | {card.init} | {card.mv} | {cells} | "
                           f"{card.dtype_label()} | {tag} |")
            out.append("")
            for card in cluster["cards"]:
                losses = cluster["beats"].get(id(card))
                if losses:
                    beaten_by = "; ".join(f"{w.full_label()} ({describe_wins(ws)})" for w, ws in losses)
                    out.append(f"- **{card.full_label()}** loses to {beaten_by}")
            out.append("")
            out.append("| Group | atk zones | blk zones | range | init (av) | mv | atk/card | blk/card |")
            out.append("|---|---|---|---|---|---|---|---|")
            for group in cluster["groups"]:
                p = profiles_by_group.get(group)
                if p:
                    out.append(f"| {p['group']} | {p['atk_zones']} | {p['blk_zones']} | {p['range']} | "
                               f"{p['init']} (av {p['init_avg']:.1f}) | {p['mv']} | "
                               f"{p['atk_per_card']:.2f} | {p['blk_per_card']:.2f} |")
            out.append("")

    if "profiles" in show:
        out.append("## Group profiles\n")
        out.append("| Group | n | atk zones | blk zones | range | init | av | mv | atk/card | blk/card | dmg types |")
        out.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for p in profiles:
            out.append(f"| {p['group']} | {p['n']} | {p['atk_zones']} | {p['blk_zones']} | {p['range']} | "
                       f"{p['init']} | {p['init_avg']:.1f} | {p['mv']} | {p['atk_per_card']:.2f} | "
                       f"{p['blk_per_card']:.2f} | {p['dtypes']} |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default=DEFAULT_CSV, help=f"card CSV to analyse (default: {DEFAULT_CSV})")
    ap.add_argument("--group", action="append", dest="groups", metavar="NAME",
                    help="restrict to this group; repeatable")
    ap.add_argument("--within", action="store_true", help="only the within-group cost check")
    ap.add_argument("--cross", action="store_true", help="only the cross-group dominance check")
    ap.add_argument("--profiles", action="store_true", help="only the group profile table")
    ap.add_argument("--markdown", action="store_true", help="emit markdown instead of aligned text")
    ap.add_argument("--include-unprinted", action="store_true", help="include PrintID=0 rows")
    args = ap.parse_args()

    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.exists(args.csv):
        ap.error(f"no such CSV: {args.csv}")

    show = {k for k in ("within", "cross", "profiles") if getattr(args, k)} or {"within", "cross", "profiles"}
    cards = load_cards(args.csv, set(args.groups) if args.groups else None, args.include_unprinted)
    if not cards:
        ap.error("no cards matched")

    profiles_by_group, profiles = group_profiles(cards)
    within = check_within(cards) if "within" in show else []
    cross = check_cross(cards) if "cross" in show else []

    render = report_markdown if args.markdown else report_text
    print(render(within, cross, profiles_by_group, profiles, show))


if __name__ == "__main__":
    main()
