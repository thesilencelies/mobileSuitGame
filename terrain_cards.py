#Generates terrain tile TEX files (build/terrain/<Name>.tex) from Terrain_square.csv rows.
#Split out of generateCards.py; called from there via create_terrain_card().

import math
from typing import Dict, List, Optional, Tuple, Union

terrain_file = "Terrain_square.csv"
terrianoutputfolder = 'build/terrain/'

terrain_images_folder = "../terrain/"
icons_folder = "../icons/"

iconwidth = "width=0.9cm"
terrain_iconwidth_value = 0.6
terarin_iconwidth = f"width={terrain_iconwidth_value}cm"

atkpointsImg = 'atkpoints.png'
defpointsImg = 'defpoints.png'
tokensImg = 'token.png'

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
TileStyle = Dict[str, Union[str, List[str]]]

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_STYLE: TileStyle = {
    "color":       "black",
    "thickness":   "semithick",
    "postaction":  "",
    "hatch":       "",
    "hatch_color": "",     # empty = same as color
    "fill":        "none",
    "text":        "",
}

def _merge_style(style: TileStyle, new_vals: TileStyle) -> None:
    """Merge new_vals into style. If a key already holds a non-default value,
    that entry becomes a list so both values are preserved."""
    for key, val in new_vals.items():
        current = style.get(key)
        default = DEFAULT_STYLE.get(key, "")
        if current is None or current == default:
            style[key] = val
        elif isinstance(current, list):
            current.append(val)
        else:
            style[key] = [current, val]


def _first_valid(val: Union[str, List[str]], default: str = "") -> str:
    """Return the first non-default entry from a possibly-list style value."""
    if isinstance(val, list):
        for v in val:
            if v and v != default:
                return v
        return default
    return val


TERRAIN_STYLE = "full"

if TERRAIN_STYLE == "border":
    ## styles for other options
    ELEVATION_1_STYLE: TileStyle = {
        "color":       "blue",
        "thickness":   "line width=3pt",
    }

    ELEVATION_2_STYLE: TileStyle = {
        "color":       "blue!50",
        "thickness":   "line width=4pt",
    }

    ELEVATION_3_STYLE: TileStyle = {
        "color":       "blue!20",
        "thickness":   "line width=4pt",
    }
    # too high to access
    IMPASSIBLE_STYLE: TileStyle = {
        "color":       "red",
        "thickness":   "line width=5pt",
        "fill":        "black",
    }

    # these should not set the line style cause they can appear at any elevation

    OBJECTIVE_STYLE: TileStyle = {
        "fill":        "green",
    }

    OBSTACLE_STYLE: TileStyle = {
        "postaction":  "postaction={draw, line width=3pt, yellow, dash pattern=on 2mm off 2mm, dash phase=0mm}",
    }

    TOKEN_STYLE: TileStyle = {
        "hatch":       "crosshatch",
        "hatch_color": "orange",     # empty = same as color
    }
elif TERRAIN_STYLE == "corner":

    ## styles for other options
    ELEVATION_1_STYLE: TileStyle = {
        # "color":       "blue",
        "thickness":   "line width=3pt",
        "icon":        "e1_filled.png",
    }

    ELEVATION_2_STYLE: TileStyle = {
        # "color":       "blue!50",
        "thickness":   "line width=4pt",
        "icon":        "e2_filled.png",
    }

    ELEVATION_3_STYLE: TileStyle = {
        # "color":       "blue!20",
        "thickness":   "line width=5pt",
        "icon":        "e3_filled.png",
    }

    # too high to access
    IMPASSIBLE_STYLE: TileStyle = {
        # "color":       "red",
        "thickness":   "line width=6pt",
        # "fill":        "black",
        "icon":        "imp_filled.png",
    }

    # these should not set the line style cause they can appear at any elevation

    OBJECTIVE_STYLE: TileStyle = {
        # "fill":        "green",
        "icon":        "obj_filled.png",
    }

    OBSTACLE_STYLE: TileStyle = {
        # "postaction":  "postaction={draw, line width=3pt, yellow, dash pattern=on 2mm off 2mm, dash phase=0mm}",
        "icon":        "obs_filled.png",
    }

    TOKEN_STYLE: TileStyle = {
        # "hatch":       "crosshatch",
        # "hatch_color": "orange",     # empty = same as color
        "icon":        "tkn_filled.png",
    }

elif TERRAIN_STYLE == "full":
    ## Elevation ramp: low elevation reads as low-saturation steel-grey blue,
    ## climbing to a vivid "city" blue at the top so taller tiles pop like
    ## glass high-rises. The base border is kept thin -- the impression of
    ## looking onto a building's side comes from the per-edge walls drawn
    ## below (see ELEVATION_WALL_PER_LEVEL_PT and _tikz_square_lines).
    ELEVATION_1_STYLE: TileStyle = {
        "color":       "cityblue!30!citysteel",
        "thickness":   "semithick",
        "icon":        "e1.png",
        "fill":        "cityblue!30!citysteel",
    }

    ELEVATION_2_STYLE: TileStyle = {
        "color":       "cityblue!60!citysteel",
        "thickness":   "semithick",
        "icon":        "e2.png",
        "fill":        "cityblue!60!citysteel",
    }

    ELEVATION_3_STYLE: TileStyle = {
        "color":       "cityblue",
        "thickness":   "semithick",
        "icon":        "e3.png",
        "fill":        "cityblue",
    }
    # too high to access
    IMPASSIBLE_STYLE: TileStyle = {
        "color":       "red",
        "thickness":   "line width=5pt",
        "fill":        "black",
        "icon":        "imp.png",
    }

    # these should not set the line style cause they can appear at any elevation

    OBJECTIVE_STYLE: TileStyle = {
        "hatch":        "vertical lines",
        "hatch_color":  "green",
        "icon":         "obj.png",
    }

    OBSTACLE_STYLE: TileStyle = {
        "hatch":       "crosshatch",
        "hatch_color": "yellow",
        "postaction":  "postaction={draw, line width=3pt, yellow, dash pattern=on 2mm off 2mm, dash phase=0mm}",
        "icon":         "obs.png",
    }

    TOKEN_STYLE: TileStyle = {
        "hatch":       "horizontal lines",
        "hatch_color": "purple",
        "icon":        "tkn.png",
    }
else:
  print("unrecognised terrain stye")
  exit()



STYLE_DICT = {
    "e1" : ELEVATION_1_STYLE,
    "e2" : ELEVATION_2_STYLE,
    "e3" : ELEVATION_3_STYLE,
    "im" : IMPASSIBLE_STYLE,
    "obs": OBSTACLE_STYLE,
    "obj": OBJECTIVE_STYLE,
    "tkn": TOKEN_STYLE
}

# Extra border width (in pt) added to a tile edge per level of elevation *drop*
# across that edge. A tile one level above its neighbour gets one increment of
# wall; a tile three levels above ground (an e3 next to the card edge) gets the
# thickest wall. This is what fakes the perspective onto a building's side.
ELEVATION_WALL_PER_LEVEL_PT = 3.5

# Which tile element codes count as elevation, and the height they represent.
_ELEVATION_LEVELS = {"e1": 1, "e2": 2, "e3": 3}


def _tile_elevation(elements: str) -> int:
    """Return the elevation level (1-3) encoded in a tile's element string.

    Tiles with no elevation code are ground level (0); the card edge is also
    treated as ground level by the caller."""
    toks = elements.split(" ")
    return max((_ELEVATION_LEVELS.get(t, 0) for t in toks), default=0)

# ---------------------------------------------------------------------------
# Line-width conversion
# ---------------------------------------------------------------------------
# TikZ standard line widths in pt (from pgfmanual).
_TIKZ_LW_PT: Dict[str, float] = {
    "ultra thin":  0.1,
    "very thin":   0.2,
    "thin":        0.4,
    "semithick":   0.6,
    "thick":       0.8,
    "very thick":  1.2,
    "ultra thick": 1.6,
}
_PT_TO_CM = 0.03528   # 1 pt = 0.03528 cm


def _thickness_to_cm(thickness: str) -> float:
    """Convert a TikZ thickness keyword or 'Xpt' / 'Xcm' string to cm."""
    t = thickness.split("=")[-1].strip().lower()
    if t in _TIKZ_LW_PT:
        return _TIKZ_LW_PT[t] * _PT_TO_CM
    if t.endswith("cm"):
        return float(t[:-2])
    if t.endswith("pt"):
        return float(t[:-2]) * _PT_TO_CM
    if t.endswith("mm"):
        return float(t[:-2]) * 0.1
    # Fallback: treat as pt
    try:
        return float(t) * _PT_TO_CM
    except ValueError:
        return _TIKZ_LW_PT["thin"] * _PT_TO_CM



# ---------------------------------------------------------------------------
# Geometry helpers  (pointy-top / flat-sided hexagons)
#
# In this orientation every hexagon has a flat horizontal edge at top and
# bottom, and pointed vertices on the left and right.
#
#   * Rows advance vertically:   y(c,r) = 3/2 * size * r
#   * Even rows have no x-shift; odd rows shift right by sqrt(3)/2 * size
#   * Columns advance:           x(c,r) = sqrt(3) * size * (c + 0.5*(r%2))
#
# Tiling periods:
#   Tx = sqrt(3) * size * cols   (horizontal repeat)
#   Ty = 3/2     * size * rows   (vertical repeat)
# ---------------------------------------------------------------------------

def hex_center(col: int, row: int, size: float, offset: Tuple[float,float]) -> Tuple[float, float]:
    """Return the (x, y) centre of hex (col, row) in cm -- pointy-top / flat-sided."""
    sq3 = math.sqrt(3)
    x = sq3 * size * (col + 0.5 * (row % 2)) + offset[0]
    y = size * 3/2 * row + offset[1]
    return x, y


def hex_corners(cx: float, cy: float, size: float) -> List[Tuple[float, float]]:
    """Return the 6 corner (x, y) pairs of a pointy-top (flat-sided) hexagon.

    Corners start at 30 deg so the top and bottom edges are horizontal."""
    return [
        (cx + size * math.cos(math.radians(30 + 60 * i)),
         cy + size * math.sin(math.radians(30 + 60 * i)))
        for i in range(6)
    ]


def inset_size(size: float, thickness: str) -> float:
    lw_cm = _thickness_to_cm(thickness)
    # For a regular hexagon, inset perpendicular to each edge by lw/2.
    # The circumradius shrinkage needed = (lw/2) / sin(pi/6) = lw/2 / 0.5 = lw.
    # (pi/6 = 30 deg is the half-angle at each vertex of a regular hexagon.)
    return size - lw_cm/2

def inset_size_square(cx: float, cy:float, size: float, thickness: str) -> float:
    lw_cm = _thickness_to_cm(thickness)
    return cx + lw_cm/2, cy + lw_cm/2, size - lw_cm

def square_center(col: int, row: int, size: float, offset: Tuple[float,float]) -> Tuple[float, float]:
    """Return the (x, y) centre of square (col, row) in cm """
    x = size * col + offset[0]
    y = size * row + offset[1]
    return x, y

def create_square(cx: float, cy: float, size: float) -> str:
    return f"({cx},{cy}) rectangle ++({size},{size})"

# ---------------------------------------------------------------------------
# LaTeX / TikZ generation
# ---------------------------------------------------------------------------

def _coord_str(corners: List[Tuple[float, float]]) -> str:
    return " -- ".join(f"({x:.4f},{y:.4f})" for x, y in corners) + " -- cycle"


def _tikz_hex_lines(col: int, row: int, size: float, s: TileStyle, offset: Tuple[float,float]) -> List[str]:
    """Return the TikZ lines that draw one hexagon.

    The stroke path uses an inset circumradius so the border is drawn
    entirely inside the nominal hex boundary
    """
    cx, cy = hex_center(col, row, size, offset)

    thickness  = _first_valid(s["thickness"],   DEFAULT_STYLE["thickness"])
    color      = _first_valid(s["color"],        DEFAULT_STYLE["color"])
    fill       = _first_valid(s.get("fill",       "none"), "none")
    postaction = _first_valid(s.get("postaction", ""),     "")

    # Full-size path for fill/hatch (covers the whole cell)
    cs_full  = _coord_str(hex_corners(cx, cy, size))
    # Inset path for the stroke (border stays inside the cell)
    r_inset  = inset_size(size, thickness)
    cs_inset = _coord_str(hex_corners(cx, cy, r_inset))

    draw_opts = [thickness, f"draw={color}", "fill opacity=0.5", postaction]

    hatch_val = s.get("hatch", "")
    hatches   = hatch_val if isinstance(hatch_val, list) else ([hatch_val] if hatch_val else [])
    hc_val    = s.get("hatch_color", "")
    hatch_colors = hc_val if isinstance(hc_val, list) else [hc_val] * len(hatches)

    lines: List[str] = []

    if hatches:
        if fill != "none":
            lines.append(f"  \\fill[fill={fill}, fill opacity=0.5] {cs_full};")
        for hatch, hc in zip(hatches, hatch_colors):
            lines.append(f"  \\fill[pattern={hatch}, fill opacity=0.5, pattern color={hc or color}] {cs_full};")
    else:
        draw_opts.append(f"fill={fill}")

    # icons
    icon_val = s.get("icon", "")
    icons = icon_val if isinstance(icon_val, list) else ([icon_val] if icon_val else [])
    hoffset = terrain_iconwidth_value / 2 + _thickness_to_cm(thickness)
    voffset = terrain_iconwidth_value / 2 + _thickness_to_cm(thickness)
    for icon in icons:
        lines.append(f'    \\node at({cx + size/2 - hoffset}, {cy + voffset})' + '{\\includegraphics[' + terarin_iconwidth + ']{' + icons_folder + icon + '}};\n')
        hoffset += terrain_iconwidth_value

    lines.append(f"  \\draw[{', '.join(draw_opts)}] {cs_inset};")
    return lines



def _tikz_square_lines(col: int, row: int, size: float, s: TileStyle, offset: Tuple[float,float],
                       side_drops: Optional[Dict[str, int]] = None) -> List[str]:
    """Return the TikZ for the given tile.

    ``side_drops`` maps 'bottom'/'top'/'left'/'right' to the number of elevation
    levels this tile stands *above* the neighbour across that edge (0 if the
    neighbour is level or higher). Edges with a positive drop are drawn as a
    thicker wall so the tile reads like a building seen from above.
    """
    if side_drops is None:
        side_drops = {}
    cx, cy = square_center(col, row, size, offset)

    thickness  = _first_valid(s["thickness"],   DEFAULT_STYLE["thickness"])
    color      = _first_valid(s["color"],        DEFAULT_STYLE["color"])
    fill       = _first_valid(s.get("fill",       "none"), "none")
    postaction = _first_valid(s.get("postaction", ""),     "")

    # Full-size path for fill/hatch (covers the whole cell)
    cs_full  = create_square(cx, cy, size)
    # Inset path used for any dashed postaction outline (kept at base width)
    cx_inset, cy_inset, r_inset  = inset_size_square(cx, cy, size, thickness)
    cs_inset = create_square(cx_inset, cy_inset, r_inset)

    hatch_val = s.get("hatch", "")
    hatches   = hatch_val if isinstance(hatch_val, list) else ([hatch_val] if hatch_val else [])
    hc_val    = s.get("hatch_color", "")
    hatch_colors = hc_val if isinstance(hc_val, list) else [hc_val] * len(hatches)

    lines: List[str] = []

    # Fill / hatch first, so the borders sit on top of it.
    if fill != "none":
        lines.append(f"  \\fill[fill={fill}, fill opacity=0.5] {cs_full};")
    for hatch, hc in zip(hatches, hatch_colors):
        lines.append(f"  \\fill[pattern={hatch}, fill opacity=0.5, pattern color={hc or color}] {cs_full};")

    # Borders, drawn one edge at a time so each side can carry its own width.
    # base_pt is the tile's normal border width; drop edges add scaled walls.
    base_pt = _thickness_to_cm(thickness) / _PT_TO_CM
    # (p1, p2, inward-unit-vector) for each named edge, corners bottom-left origin.
    edges = {
        "bottom": ((cx,        cy),        (cx + size, cy),        (0.0,  1.0)),
        "top":    ((cx,        cy + size), (cx + size, cy + size), (0.0, -1.0)),
        "left":   ((cx,        cy),        (cx,        cy + size), (1.0,  0.0)),
        "right":  ((cx + size, cy),        (cx + size, cy + size), (-1.0, 0.0)),
    }
    for name, (p1, p2, (dx, dy)) in edges.items():
        drop = side_drops.get(name, 0)
        edge_pt = base_pt + drop * ELEVATION_WALL_PER_LEVEL_PT
        inset = (edge_pt * _PT_TO_CM) / 2  # keep the stroke inside the cell
        x1, y1 = p1[0] + dx * inset, p1[1] + dy * inset
        x2, y2 = p2[0] + dx * inset, p2[1] + dy * inset
        lines.append(f"  \\draw[line width={edge_pt:.2f}pt, draw={color}] "
                     f"({x1:.4f},{y1:.4f}) -- ({x2:.4f},{y2:.4f});")

    # Preserve any dashed postaction outline (e.g. obstacles).
    if postaction:
        lines.append(f"  \\path[{postaction}] {cs_inset};")

    lines.append("")
    # icons
    icon_val = s.get("icon", "")
    icons = icon_val if isinstance(icon_val, list) else ([icon_val] if icon_val else [])
    hoffset = terrain_iconwidth_value / 2 + _thickness_to_cm(thickness)
    voffset = terrain_iconwidth_value / 2 + _thickness_to_cm(thickness)
    for icon in icons:
        lines.append(f'    \\node at({cx + size - hoffset}, {cy + voffset})' + '{\\includegraphics[' + terarin_iconwidth + ']{' + icons_folder + icon + '}};\n')
        hoffset += terrain_iconwidth_value
        if hoffset > size - terrain_iconwidth_value / 2 + _thickness_to_cm(thickness):
            hoffset = terrain_iconwidth_value / 2 + _thickness_to_cm(thickness)
            voffset += terrain_iconwidth_value

    return lines


def create_terrain_card(row):
    """populates the terrain including correct borders"""
    with open(terrianoutputfolder + row["Name"] + '.tex', 'w') as ofile:
        #load the background image
        terrain_text = "\\begin{tikzpicture}[backbox/.style= {rectangle, minimum height = 8.9cm," \
                + " minimum width =6.35cm, rounded corners = 0.3cm, fill=white, opacity=0.75}]\n "
        terrain_text += "\\node [rectangle, minimum width = 6.4cm, minimum height = 8.7cm, fill=black!10!white!90] at (3.25,4.5){};\n"
        terrain_text += '\\node [opacity=0.6] at (3.25,4.45){\\includegraphics[width=6.35cm, max height = 8.85cm,' +\
                'keepaspectratio]{' + terrain_images_folder + row["CardImg"] + '}};\n'

        # terrain card size
        cols = 3
        rows = 4
        hex_size = 2.06 #cm - diameter

        hoffset = 0.1
        voffset = 0.2

        # superimpose the grid
        # put height/terrain information in where relevant (borders?)
        # col_range = range(-1, cols + 1)
        # row_range = range(-1, rows + 1)
        col_range = range(0, cols)
        row_range = range(0, rows)

        # Elevation of each in-grid tile; anything off the card is ground (0).
        def elev_at(cc: int, rr: int) -> int:
            if 0 <= cc < cols and 0 <= rr < rows:
                return _tile_elevation(row[f"tile_{rr}_{cc}"])
            return 0

        hex_lines: List[str] = []
        for c in col_range:
            for r in row_range:
                style = dict(DEFAULT_STYLE)
                if 0 <= c < cols and 0 <= r < rows:
                    for element in row[f"tile_{r}_{c}"].split(" "):
                        _merge_style(style, STYLE_DICT.get(element, {}))
                # Thicken the edges that look down onto lower neighbours, scaled
                # by how far the drop is, to fake perspective onto building sides.
                e = elev_at(c, r)
                side_drops = {
                    "bottom": max(e - elev_at(c,     r - 1), 0),
                    "top":    max(e - elev_at(c,     r + 1), 0),
                    "left":   max(e - elev_at(c - 1, r),     0),
                    "right":  max(e - elev_at(c + 1, r),     0),
                }
                # hex_lines.append("\n".join(_tikz_hex_lines(c, r + 1, hex_size, style, (hoffset, voffset))))
                hex_lines.append("\n".join(_tikz_square_lines(c, r, hex_size, style, (hoffset, voffset), side_drops)))


        inner_body = "\n".join(hex_lines)

        # TODO - add this clipping to every card
        clip_line = f"  \\clip ({hoffset},{voffset}) rectangle (6.4, 8.9);"
        terrain_text += "\\begin{scope}\n" + clip_line + "\n" + inner_body + "\n" + "\\end{scope}\n"

        # add rules text if extant (probably an objective card)

        if row["Rules"]:
            terrain_text += "\\node[rectangle, fill = white, opacity = 0.75, minimum height =1.8cm, rounded corners = 0.3cm, " \
                    + "text width = 3.1cm]  at (4.6, 2.1){\\footnotesize{" + row['Rules'] +"}};\n"


        # add objective information symbols
        if int(row["Attack Points"]):
            terrain_text += '\\node at(1, 8.2){\\includegraphics[' + iconwidth + ']{' + icons_folder + atkpointsImg + '}};\n'
            terrain_text += "\\node at (1, 8.2){\\Large{\\textbf{" + row['Attack Points'] +"}}};\n"
        if int(row["Defend Points"]):
            terrain_text += '\\node at(2, 8.2){\\includegraphics[' + iconwidth + ']{' + icons_folder + defpointsImg + '}};\n'
            terrain_text += "\\node at (2, 8.2){\\Large{\\textbf{" + row['Defend Points'] +"}}};\n"
        if int(row["Tokens"]):
            terrain_text += '\\node at(3, 8.2){\\includegraphics[' + iconwidth + ']{' + icons_folder + tokensImg + '}};\n'
            terrain_text += "\\node at (3, 8.2){\\large{\\textbf{" + row['Tokens'] +"}}};\n"


        #finish the tikzpicture
        terrain_text += "\\end{tikzpicture}\n"

        ofile.write(terrain_text)
        return terrain_text + "~"
