#!/usr/bin/env python3
"""
weapon_fingerprint_report.py

Generates a single scrollable HTML page with one row per weapon Group, showing:
  - average attack/block dice by zone (High/Mid/Low)
  - which ability/status keywords appear anywhere in that group's card text
  - the spread (min-mean-max) of Initiative and Movement across the group's cards

Meant as a companion to plotCardStatistics.py: that tool answers "how does the
whole roster balance out", this one answers "how distinct does each weapon feel".

Run: python weapon_fingerprint_report.py [--output build/weapon_fingerprint.html] [--open]
"""

import argparse
import re
import webbrowser
from pathlib import Path

import pandas as pd

from generateCards import ability_dict, numbered_ability_dict, status_dict

WEAPON_CSV = "Weapon actions.csv"
ZONES = ["High", "Mid", "Low"]  # display order, matches card layout top-to-bottom

# ── Palette (dataviz skill reference palette; roles fixed per series identity) ──
COLOR_ATTACK = ("#e34948", "#e66767")       # categorical slot 6 (red)
COLOR_BLOCK = ("#2a78d6", "#3987e5")        # categorical slot 1 (blue)
COLOR_INITIATIVE = ("#4a3aa7", "#9085e9")   # categorical slot 5 (violet)
COLOR_MOVEMENT = ("#1baf7a", "#199e70")     # categorical slot 2 (aqua)


def parse_numeric_field(val):
    """Handles blank/comma-separated/plus-prefixed numeric CSV fields (e.g. '8,3', '+5')."""
    if pd.isna(val):
        return None
    s = str(val).strip().lstrip('+')
    if not s:
        return None
    try:
        return float(s.split(',')[0])
    except ValueError:
        return None


def build_keyword_patterns():
    """Maps each display keyword name to a compiled regex matching its LaTeX macro
    (with or without the \\full... verbose variant) anywhere in a card's Text field."""
    patterns = {}
    for name in ability_dict:
        cmd = re.escape(name.lower().replace(" ", ""))
        patterns[name] = re.compile(r'\\(full)?' + cmd + r'(?![a-zA-Z])')
    for name in numbered_ability_dict:
        cmd = re.escape(name.lower().replace(" ", ""))
        patterns[name] = re.compile(r'\\(full)?' + cmd + r'(?![a-zA-Z])')
    for name in status_dict:
        cmd = re.escape(name.lower().replace(" ", ""))
        patterns[name] = re.compile(r'\\(full)?' + cmd + r'(?![a-zA-Z])')
    return patterns


KEYWORD_PATTERNS = build_keyword_patterns()


def load_groups():
    df = pd.read_csv(WEAPON_CSV)
    df = df[df["PrintID"].fillna(0).astype(int) > 0].copy()

    for z in ZONES:
        df[f"{z}Attack"] = pd.to_numeric(df[f"{z}Attack"], errors="coerce").fillna(0)
        df[f"{z}Block"] = pd.to_numeric(df[f"{z}Block"], errors="coerce").fillna(0)
    df["Initiative_p"] = df["Initiative"].apply(parse_numeric_field)
    df["Movement_p"] = df["Movement"].apply(parse_numeric_field)

    groups = []
    for group, rows in df.groupby("Group", sort=True):
        zone_stats = {
            z: {"attack": rows[f"{z}Attack"].mean(), "block": rows[f"{z}Block"].mean()}
            for z in ZONES
        }
        all_text = " ".join(str(t) for t in rows["Text"].dropna())
        keywords = [name for name, pat in KEYWORD_PATTERNS.items() if pat.search(all_text)]

        init_vals = sorted(v for v in rows["Initiative_p"] if v is not None)
        move_vals = sorted(v for v in rows["Movement_p"] if v is not None)

        groups.append({
            "name": group,
            "count": len(rows),
            "zone_stats": zone_stats,
            "keywords": keywords,
            "init_vals": init_vals,
            "move_vals": move_vals,
        })
    return groups


# ── SVG mini-chart rendering ────────────────────────────────────────────────
# Attack and block get their own single-series chart each (rather than grouped
# bars) so each can be read independently at a glance.
BAR_W, ZONE_GAP, PAD_X = 18, 12, 6
CHART_TOP_PAD, BAR_MAX_H, LABEL_H = 12, 40, 14
BASELINE_Y = CHART_TOP_PAD + BAR_MAX_H
ZONE_CHART_W = PAD_X * 2 + BAR_W * 3 + ZONE_GAP * 2
ZONE_CHART_H = BASELINE_Y + LABEL_H


def _rounded_top_rect(x, y, w, h, r=3):
    if h <= 0.5:
        return ""
    r = min(r, w / 2, h)
    top = BASELINE_Y
    return (f"M{x:.2f},{top} L{x:.2f},{y + r:.2f} "
            f"Q{x:.2f},{y:.2f} {x + r:.2f},{y:.2f} "
            f"L{x + w - r:.2f},{y:.2f} Q{x + w:.2f},{y:.2f} {x + w:.2f},{y + r:.2f} "
            f"L{x + w:.2f},{top} Z")


def render_single_series_chart(zone_stats, global_max, key, cls, label):
    """One bar per zone (High/Mid/Low) for a single series (attack OR block)."""
    parts = [f'<svg viewBox="0 0 {ZONE_CHART_W} {ZONE_CHART_H}" width="{ZONE_CHART_W}" height="{ZONE_CHART_H}" class="zone-chart">']
    parts.append(f'<line x1="{PAD_X}" y1="{BASELINE_Y}" x2="{ZONE_CHART_W - PAD_X}" y2="{BASELINE_Y}" class="baseline"/>')
    for i, zone in enumerate(ZONES):
        x = PAD_X + i * (BAR_W + ZONE_GAP)
        val = zone_stats[zone][key]
        h = (val / global_max) * BAR_MAX_H if global_max else 0
        y = BASELINE_Y - h
        path = _rounded_top_rect(x, y, BAR_W, h)
        if path:
            parts.append(f'<path d="{path}" class="{cls}"><title>{zone} {label}: {val:.2f} avg dice</title></path>')
            if val >= 0.05:
                parts.append(f'<text x="{x + BAR_W / 2:.2f}" y="{y - 2:.2f}" class="bar-label">{val:.1f}</text>')
        parts.append(f'<text x="{x + BAR_W / 2:.2f}" y="{BASELINE_Y + 11}" class="zone-label">{zone[0]}</text>')
    parts.append('</svg>')
    return "".join(parts)


RANGE_W, RANGE_H, RANGE_PAD = 130, 30, 10
RANGE_TRACK_Y = 14


def render_range_bar(values, domain_min, domain_max, color_light, color_dark, unit_label):
    span = (domain_max - domain_min) or 1
    def sx(v):
        return RANGE_PAD + (v - domain_min) / span * (RANGE_W - 2 * RANGE_PAD)

    parts = [f'<svg viewBox="0 0 {RANGE_W} {RANGE_H}" width="{RANGE_W}" height="{RANGE_H}" class="range-chart">']
    parts.append(f'<line x1="{RANGE_PAD}" y1="{RANGE_TRACK_Y}" x2="{RANGE_W - RANGE_PAD}" y2="{RANGE_TRACK_Y}" class="range-track"/>')
    parts.append(f'<text x="{RANGE_PAD}" y="{RANGE_H}" class="range-domain-label" text-anchor="start">{domain_min:g}</text>')
    parts.append(f'<text x="{RANGE_W - RANGE_PAD}" y="{RANGE_H}" class="range-domain-label" text-anchor="end">{domain_max:g}</text>')

    if not values:
        parts.append('</svg>')
        return "".join(parts)

    vmin, vmax = values[0], values[-1]
    mean = sum(values) / len(values)
    x_min, x_max, x_mean = sx(vmin), sx(vmax), sx(mean)

    if vmax > vmin:
        parts.append(f'<line x1="{x_min:.2f}" y1="{RANGE_TRACK_Y}" x2="{x_max:.2f}" y2="{RANGE_TRACK_Y}" class="range-span" stroke="{color_light}"/>')
        parts.append(f'<text x="{x_min:.2f}" y="{RANGE_TRACK_Y - 6}" class="range-end-label" text-anchor="middle">{vmin:g}</text>')
        parts.append(f'<text x="{x_max:.2f}" y="{RANGE_TRACK_Y - 6}" class="range-end-label" text-anchor="middle">{vmax:g}</text>')
    else:
        parts.append(f'<text x="{x_min:.2f}" y="{RANGE_TRACK_Y - 6}" class="range-end-label" text-anchor="middle">{vmin:g}</text>')

    parts.append(f'<circle cx="{x_mean:.2f}" cy="{RANGE_TRACK_Y}" r="4" class="range-mean" fill="{color_light}"><title>{unit_label} mean: {mean:.1f} (n={len(values)})</title></circle>')
    parts.append('</svg>')
    return "".join(parts)


KEYWORD_CATEGORY = {**{k: "ability" for k in ability_dict}, **{k: "ability" for k in numbered_ability_dict},
                    **{k: "status" for k in status_dict}}


def render_keywords(keywords):
    if not keywords:
        return '<span class="kw-empty">&mdash;</span>'
    order = list(ability_dict) + list(numbered_ability_dict) + list(status_dict)
    ordered = [k for k in order if k in keywords]
    return "".join(f'<span class="kw-badge kw-{KEYWORD_CATEGORY[k]}">{k}</span>' for k in ordered)


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Weapon Fingerprint Report</title>
<style>
  :root {{
    --surface-1: #fcfcfb; --page: #f9f9f7;
    --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #898781;
    --gridline: #e1e0d9; --baseline: #c3c2b7; --border: rgba(11,11,11,0.10);
    --attack: {attack_l}; --block: {block_l}; --initiative: {init_l}; --movement: {move_l};
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --surface-1: #1a1a19; --page: #0d0d0d;
      --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #898781;
      --gridline: #2c2c2a; --baseline: #383835; --border: rgba(255,255,255,0.10);
      --attack: {attack_d}; --block: {block_d}; --initiative: {init_d}; --movement: {move_d};
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--page); color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  }}
  header {{
    position: sticky; top: 0; z-index: 2; background: var(--page);
    border-bottom: 1px solid var(--border); padding: 16px 24px;
  }}
  h1 {{ font-size: 18px; margin: 0 0 4px; }}
  .subtitle {{ color: var(--text-secondary); font-size: 13px; margin: 0 0 12px; }}
  .legend {{ display: flex; gap: 18px; flex-wrap: wrap; font-size: 12px; color: var(--text-secondary); }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; }}
  .swatch {{ width: 10px; height: 10px; border-radius: 2px; display: inline-block; }}
  .swatch.line {{ border-radius: 5px; width: 14px; height: 4px; }}

  .table {{ display: grid; grid-template-columns: 160px 100px 100px 140px 140px 1fr; }}
  .head-row, .row {{
    display: grid; grid-template-columns: subgrid; grid-column: 1 / -1;
    align-items: center; padding: 10px 24px; column-gap: 16px;
  }}
  .head-row {{
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em;
    color: var(--text-muted); border-bottom: 1px solid var(--border);
  }}
  .row {{ border-bottom: 1px solid var(--gridline); }}
  .row:hover {{ background: color-mix(in srgb, var(--text-primary) 4%, transparent); }}
  .weapon-name {{ font-weight: 600; font-size: 13px; }}
  .weapon-count {{ color: var(--text-muted); font-size: 11px; }}

  .baseline {{ stroke: var(--baseline); stroke-width: 1; }}
  .bar-attack {{ fill: var(--attack); }}
  .bar-block {{ fill: var(--block); }}
  .bar-label {{ font-size: 7px; fill: var(--text-secondary); text-anchor: middle; }}
  .zone-label {{ font-size: 8px; fill: var(--text-muted); text-anchor: middle; }}

  .range-track {{ stroke: var(--gridline); stroke-width: 1; }}
  .range-span {{ stroke-width: 4; stroke-linecap: round; }}
  .range-mean {{ stroke: var(--surface-1); stroke-width: 2; }}
  .range-end-label {{ font-size: 8px; fill: var(--text-secondary); }}
  .range-domain-label {{ font-size: 7px; fill: var(--text-muted); }}

  .kw-badge {{
    display: inline-block; font-size: 10.5px; padding: 2px 7px; margin: 2px 4px 2px 0;
    border-radius: 10px; border: 1px solid var(--border); color: var(--text-secondary);
    white-space: nowrap;
  }}
  .kw-status {{ border-style: dashed; }}
  .kw-empty {{ color: var(--text-muted); font-size: 12px; }}

  footer {{ padding: 20px 24px 40px; color: var(--text-muted); font-size: 11px; }}
</style>
</head>
<body>
<header>
  <h1>Weapon Fingerprint Report</h1>
  <p class="subtitle">One row per weapon group &mdash; {n_groups} groups, {n_cards} printed cards. Bar heights share one scale across every row.</p>
  <div class="legend">
    <span class="legend-item"><span class="swatch" style="background:var(--attack)"></span>Attack (avg dice)</span>
    <span class="legend-item"><span class="swatch" style="background:var(--block)"></span>Block (avg dice)</span>
    <span class="legend-item"><span class="swatch line" style="background:var(--initiative)"></span>Initiative spread</span>
    <span class="legend-item"><span class="swatch line" style="background:var(--movement)"></span>Movement spread</span>
    <span class="legend-item"><span class="kw-badge" style="padding:1px 6px;">keyword</span>Ability keyword present</span>
    <span class="legend-item"><span class="kw-badge kw-status" style="padding:1px 6px;">status</span>Status effect inflicted</span>
  </div>
</header>
<div class="table">
  <div class="head-row">
    <div>Weapon</div><div>Attack (H&nbsp;&middot;&nbsp;M&nbsp;&middot;&nbsp;L)</div><div>Block (H&nbsp;&middot;&nbsp;M&nbsp;&middot;&nbsp;L)</div><div>Initiative</div><div>Movement</div><div>Keywords</div>
  </div>
  {rows}
</div>
<footer>Generated by weapon_fingerprint_report.py from {csv_name}. Re-run after editing weapon data to refresh.</footer>
</body>
</html>
"""

ROW_TEMPLATE = """<div class="row">
  <div><div class="weapon-name">{name}</div><div class="weapon-count">{count} card{plural}</div></div>
  <div>{attack_chart}</div>
  <div>{block_chart}</div>
  <div>{init_chart}</div>
  <div>{move_chart}</div>
  <div>{keywords}</div>
</div>"""


def render_page(groups, domains):
    global_max = max(
        max(zs["attack"], zs["block"]) for g in groups for zs in [g["zone_stats"][z] for z in ZONES]
    ) or 1

    rows_html = []
    for g in groups:
        rows_html.append(ROW_TEMPLATE.format(
            name=g["name"],
            count=g["count"],
            plural="" if g["count"] == 1 else "s",
            attack_chart=render_single_series_chart(g["zone_stats"], global_max, "attack", "bar-attack", "Attack"),
            block_chart=render_single_series_chart(g["zone_stats"], global_max, "block", "bar-block", "Block"),
            keywords=render_keywords(g["keywords"]),
            init_chart=render_range_bar(g["init_vals"], *domains["init"], *COLOR_INITIATIVE, "Initiative"),
            move_chart=render_range_bar(g["move_vals"], *domains["move"], *COLOR_MOVEMENT, "Movement"),
        ))

    return PAGE_TEMPLATE.format(
        attack_l=COLOR_ATTACK[0], attack_d=COLOR_ATTACK[1],
        block_l=COLOR_BLOCK[0], block_d=COLOR_BLOCK[1],
        init_l=COLOR_INITIATIVE[0], init_d=COLOR_INITIATIVE[1],
        move_l=COLOR_MOVEMENT[0], move_d=COLOR_MOVEMENT[1],
        n_groups=len(groups), n_cards=sum(g["count"] for g in groups),
        rows="\n  ".join(rows_html), csv_name=WEAPON_CSV,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="build/weapon_fingerprint.html")
    parser.add_argument("--open", action="store_true", help="Open the report in a browser after generating it")
    args = parser.parse_args()

    groups = load_groups()
    if not groups:
        raise SystemExit(f"No printed weapon rows found in {WEAPON_CSV}")

    all_init = [v for g in groups for v in g["init_vals"]]
    all_move = [v for g in groups for v in g["move_vals"]]
    domains = {
        "init": (min(all_init), max(all_init)),
        "move": (min(all_move), max(all_move)),
    }

    html = render_page(groups, domains)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"Wrote {out_path} ({len(groups)} weapon groups)")

    if args.open:
        webbrowser.open(out_path.resolve().as_uri())


if __name__ == "__main__":
    main()
