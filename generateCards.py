#Reads the given CSV of card designs and turns them into TEX files that could be imported as needed

import csv
import enum
import math
from typing import Dict, Tuple, Optional, List

weapon_actions_file = 'Weapon actions.csv'
general_action_file = 'Basic actions.csv'
pilot_actions_file = 'Pilot actions.csv'
booster_actions_file = 'Booster actions.csv'
terrain_file = "Terrain.csv"
frames_file = 'Frames.csv'

cardoutputfolder='build/card_'
frameoutputfolder='build/frame_'
terrianoutputfolder='build/terrain_'

#icon names
cutAtkImg = 'attackImg.png'
bulletAtkImg = 'rattackImg.png'
bludgeonAtkImg = "hammerAttackImg.png"
pierceAtkImg = "pierceAttackImg.png"

blkImg = 'blockImg.png'
rangeImg = 'rangeImg.png'
initImg = 'initImg.png'
mvImg = 'mvimg.png'
weaponImg = 'weapon.png'
boosterImg = 'boosterImg.png'
pointsImg = 'points.png'
tokensImg = 'token.png'

images_folder = "../pictures/"
terrain_images_folder = "../terrain/"
frame_images_folder = "../pictures/"
icons_folder = "../icons/"


# frameBackgrounds = ["Ouwa_frame_1.jpeg","Aegis_frame_1.jpeg", "Guild_frame_1.png",
#                     "Collective_frame_1.jpeg", "CotN_frame_1.jpeg", "Revolution_frame_1.jpeg"]

iconwidth = "width=0.9cm"
inline_iconwidth = "width=0.5cm"

header_text = "\\documentclass[a4paper, landscape]{article}\n \\usepackage[left =2cm, right = 2cm, " \
            + "top = 1.4cm, bottom =1.4cm]{geometry} \n \\usepackage{tikz} \n \\usepackage[export]{adjustbox}" \
            + "\n \\usetikzlibrary{positioning} \n \\usetikzlibrary{patterns} \n"

begin_doc = "\\begin{document}\n\\noindent\n"

class CardTypeEnum(enum.Enum):
    BASIC = 0
    WEAPON = 1
    PILOT = 2
    BOOSTER = 3

damage_type_dict = {
    "cut" : cutAtkImg,
    "pierce" : pierceAtkImg,
    "impact" : bludgeonAtkImg,
    "projectile" : bulletAtkImg
    }

def createMacros():
    with open(cardoutputfolder + 'macros.tex', 'w') as ofile:
        card_text = ""

        for t, img in damage_type_dict.items():
            card_text += "\n\\newcommand{\\" + t + "}{"
            card_text += '\\includegraphics[' + iconwidth + ']{' + icons_folder + img + '}'
            card_text += "}\n\\newcommand{\\small" + t + "}{"
            card_text += '\\includegraphics[' + inline_iconwidth + ']{' + icons_folder + img + '}'
            card_text += "}\n"

        ofile.write(card_text)
        return card_text


def getTypeName(t: CardTypeEnum):
    return str(t).split(".")[-1].lower().capitalize()

def attack_box(atk, rng, block, pos, dmg_type):
    out_text = ""
    # the attack box at the requested location
    if atk or block:
        out_text = out_text + "\\node[backbox] at (6.2, " + str(pos) +"){};\n"
    # what graphic to use
    aimg = "\\" + dmg_type

    for d in range(0, atk):
        out_text = out_text + "\\node at (" + str(
            -(d / 2) + 7.0) + ', ' + str(pos + 0.5) + '){' + aimg + '};\n'

    # blocks
    for d in range(0, block):
        out_text = out_text + "\\node at (" + str(
            -(d / 2) + 7.0) + ', ' + str(pos - 0.5) + '){\\includegraphics[' + iconwidth + ']{' + icons_folder + \
                   blkImg + '}};\n'
    # ranges
    if rng > 0:
        out_text = out_text + '\\node at ( 5.9, ' + str(pos - 0.55) + '){\\includegraphics[' + iconwidth + ']{' + \
                   icons_folder + rangeImg + '}};\n'
        out_text = out_text + '\\node at (5.9, ' + str(pos - 0.1) + '){\\Large{' + str(rng) + '}};\n'

    return out_text


def make_card_from_row(row, i, card_type):
    with open(cardoutputfolder + row['Group'] + "_" + str(i) + '.tex', 'w') as ofile:
        # art and card edge
        card_text = "\\begin{tikzpicture}[scale=0.86, backbox/.style= {rectangle, minimum height = 2.0cm," \
                   + " minimum width =2.0cm, rounded corners = 0.3cm, fill=white, opacity=0.75}]\n "
        card_text = card_text + "\\node [rectangle, minimum width = 6.2cm, minimum height = 8.5cm, fill=black] at (4,5){};\n"
        card_text = card_text + '\\node at (4,5){\\includegraphics[width=6cm, max height = 8.3cm,' +\
              ' keepaspectratio]{' + images_folder + row["BackgroundImg"] + '}};\n'
        # frame style - these need designing
        if card_type is CardTypeEnum.BOOSTER:
            # TODO - create booster frame
            pass
        if card_type is CardTypeEnum.PILOT:
            # TODO - create pilot frame
            pass
        if card_type is CardTypeEnum.WEAPON:
            # TODO - create weapon frame
            pass
        
        # name and faction
        card_text = card_text + "\\node [rectangle, minimum width=4cm, minimum height = 0.6cm,rounded corners = 0.1cm," +\
                "fill=white, opacity=0.75, text width=4.1cm] at (4, 9.2){\\large{" + row["Name"]
        if row["Faction"]:
            card_text = card_text +  "}\\\\\n\\small{\\emph{~" + row["Faction"] + "}"
        card_text = card_text +  "}};\n"

        # default symbols
        card_text = card_text + '\\node at(1, 9.2){\\includegraphics[' + iconwidth + ']{' + icons_folder + initImg + '}};\n'
        card_text = card_text + "\\node at (1, 9.2){\\Large{\\textbf{" + row['Initiative'] +"}}};\n"
        card_text = card_text + '\\node at (1.1, 8.2){\\includegraphics[' + iconwidth + ']{' + icons_folder + mvImg + '}};\n'
        card_text = card_text + " \\node at (1, 8.2){\\Large{\\textbf{" + row['Movement'] +"}}};\n"

        if int(row["OneUse"]) > 0:
             card_text = card_text + "\\node at (7,9.2)[circle, fill = red]{\\large{\\textbf{O}}};\n"

        try:
            if card_type is CardTypeEnum.PILOT:
                card_text = card_text + attack_box(0, 0, 1, 7.5, "")
            else:
                card_text = card_text + attack_box(int(row["HighAttack"]), int(row["HighRange"]), int(row["HighBlock"]), 7.5, row["HighDType"])
                card_text = card_text + attack_box(int(row["MidAttack"]), int(row["MidRange"]), int(row["MidBlock"]), 5.0, row["MidDType"])
                card_text = card_text + attack_box(int(row["LowAttack"]), int(row["LowRange"]), int(row["LowBlock"]), 2.5, row["LowDType"])
        except:
            print(f"exception for {row['Name']}")
            return ""

        # textbox
        if card_type is CardTypeEnum.PILOT:
            card_text = card_text + "\\node[rectangle, fill = white, opacity = 0.75, minimum height =2.5cm, rounded corners = 0.1cm, " \
                    + "text width = 5.4cm]  at (4, 3.5){\\small{" + row['Text'] +"}};\n"
        else:
            if row["Text"]:
                card_text = card_text + "\\node[rectangle, fill = white, opacity = 0.75, minimum height =1.5cm, rounded corners = 0.1cm, " \
                    + "text width = 3.5cm]  at (2.75, 3.5){\\small{" + row['Text'] +"}};\n"
        
        #set info
        card_text = card_text + "\\node[rectangle, fill = white, opacity = 0.75, minimum width=6cm, minimum height =0.8cm, " \
                + "rounded corners = 0.1cm, text width = 5.8cm]  at (4, 0.7){" \
                + row["Faction"] + " " + getTypeName(card_type) +  " \\hfill " + row['Group'] + "\\\\" + \
                "\\footnotesize{\\emph{" + row["Flavor"] + "}}};\n"


        card_text = card_text + "\\end{tikzpicture}\n"
        # ofile.write(header_text)
        ofile.write(card_text)
        # ofile.write("\\end{document}\n")
        return card_text + "~"

def draw_armor(armor, position, penalty):
    # old style
#    rval = "\\node [rectangle, minimum width=2cm, minimum height = 1cm, fill = red, opacity = 0.75] at (6.5, "  + position + "){" + armor +"};\n"
    rval = ""
    # bars
    horizontal_pos = 7
    for i in range(armor):
        rval += "\\node [anchor=east, rectangle, rounded corners = 0.1cm, minimum width=0.9cm, minimum height=0.6cm, draw, fill=red, opacity=0.8, rotate=90] at "+\
            "(" + str(horizontal_pos) + ", " + str(position) + "){"
        if i == armor - 1:
            rval += "\\tiny{" + penalty + "}"
        rval += "};\n"
        horizontal_pos -= 0.7

    return rval

def create_frame_sheet(frame, i):
    """creates the frames datasheet procedurally from the given data"""
    with open(frameoutputfolder + str(i) + '.tex', 'w') as ofile:
        #load the initial image
        frame_text = "\\begin{tikzpicture}[scale=0.86, backbox/.style= {rectangle, minimum height = 2.2cm," \
                + " minimum width =2.2cm, rounded corners = 0.3cm, fill=white, opacity=0.75}]\n "
        frame_text = frame_text + "\\node [rectangle, minimum width = 6.2cm, minimum height = 8.5cm, fill=black!70!white!30] at (4,5){};\n"
        frame_text = frame_text + '\\node at (4,5){\\includegraphics[width=6cm, max height = 8.3cm, keepaspectratio]{' + frame_images_folder + frame["BackgroundImg"] + '}};\n'
        # name
        frame_text = frame_text + "\\node [rectangle, minimum width=4.3cm, minimum height = 1cm,rounded corners = 0.1cm, fill=white, opacity=0.75, text width=4.1cm]" +\
                            "at (3.3, 9){\\large{" + frame["Name"] + "}\\\\\n\\small{\\emph{~" + frame["Faction"] + "}}};\n"
        
        # movement
        frame_text = frame_text + '\\node at (7,9){\\includegraphics[' + iconwidth + ']{' + icons_folder + mvImg + '}};\n'
        frame_text = frame_text + " \\node at (7,9){\\Large{\\textbf{" + frame['Movement'] +"}}};\n"
        
        # armor
        frame_text = frame_text + draw_armor(int(frame["Top armour"]), 8.5, "-1Init")
        frame_text = frame_text + draw_armor(int(frame["Side armour"]), 7, "-1Crd")
        frame_text = frame_text + draw_armor(int(frame["Low armour"]), 5.5, "-1Mv")
        
        # ability
        frame_text = frame_text + "\\node[rectangle, fill = white, opacity = 0.75, minimum height =1.5cm, rounded corners = 0.1cm, " \
                    + "text width = 5cm]  at (4, 3.5){\\small{" + frame['Abilities'] +"}};\n"

        # weapons
        frame_text = frame_text + "\\node [rectangle, rounded corners = 0.1cm, minimum width=5.5cm, minimum height = 1.8cm, fill = white," + \
                " opacity = 0.75] at (4, 1.2)(bottom_box){};\n"
        frame_text = frame_text + "\\node[anchor=north west, text width = 5.2cm] at (bottom_box.north west){" \
                '\\includegraphics[' + inline_iconwidth + ']{' + icons_folder + weaponImg + '} \\large{ : ' + str(frame["Weapon Slots"]) +  \
                '} ~\\includegraphics[' + inline_iconwidth + ']{' + icons_folder + boosterImg + '}\\large{  : ' + str(frame["Boosters"]) + \
                 "} \\\\\\emph{\\footnotesize{" + frame["Flavor"] +  "}}};\n"


        #finish the tikzpicture
        frame_text = frame_text + "\\end{tikzpicture}\n"

        ofile.write(frame_text)
        return frame_text + "~"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
HexStyle = Dict[str, str]
StyleMap  = Dict[Tuple[int, int], HexStyle]   # (col, row) -> style
 
# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_STYLE: HexStyle = {
    "color":       "black",
    "thickness":   "thin",
    "postaction":  "",
    "hatch":       "",
    "hatch_color": "",     # empty = same as color
    "fill":        "none",
}

## styles for other options
ELEVATION_1_STYLE: HexStyle = {
    "color":       "black!70",
    "thickness":   "thick",
    "postaction":  "",
    "hatch":       "",
    "hatch_color": "",     # empty = same as color
    "fill":        "blue!20",
}

ELEVATION_2_STYLE: HexStyle = {
    "color":       "black!30",
    "thickness":   "thick",
    "postaction":  "",
    "hatch":       "",
    "hatch_color": "",     # empty = same as color
    "fill":        "green!20",
}

IMPASSIBLE_STYLE: HexStyle = {
    "color":       "yellow",
    "thickness":   "thick",
    "postaction":  "postaction={draw, line width=0.8pt, black, dash pattern=on 0.2mm off 0.2mm, dash phase=0mm}",
    "hatch":       "",
    "hatch_color": "",     # empty = same as color
    "fill":        "black",
}

OBJECTIVE_STYLE: HexStyle = {
    "color":       "green",
    "thickness":   "thick",
    "postaction":  "",
    "hatch":       "crosshatch",
    "hatch_color": "yellow!70",     
    "fill":        "none",
}

OBSTACLE_STYLE: HexStyle = {
    "color":       "red",
    "thickness":   "very thick",
    "postaction":  "",
    "hatch":       "",
    "hatch_color": "",     # empty = same as color
    "fill":        "none",
}


STYLE_DICT = {
    "e1" : ELEVATION_1_STYLE,
    "e2" : ELEVATION_2_STYLE,
    "im" : IMPASSIBLE_STYLE,
    "obs": OBSTACLE_STYLE,
    "obj": OBJECTIVE_STYLE
}

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
    t = thickness.strip().lower()
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




# # ---------------------------------------------------------------------------
# # Geometry helpers  (flat-top hexagons)
# # ---------------------------------------------------------------------------
 
# def hex_center(col: int, row: int, size: float) -> Tuple[float, float]:
#     """Return the (x, y) centre of hex (col, row) in cm."""
#     x = size * 3/2 * col + 3/4
#     y = size* math.sqrt(3) * (row + 0.5 * (col % 2)) + math.sqrt(3) / 2
#     return x, y
 
 
# def hex_corners(cx: float, cy: float, size: float):
#     """Return the 6 corner (x, y) pairs of a flat-top hexagon."""
#     return [
#         (cx + size * math.cos(math.radians(60 * i)),
#          cy + size * math.sin(math.radians(60 * i)))
#         for i in range(6)
#     ]
 

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
    return size - lw_cm

# ---------------------------------------------------------------------------
# LaTeX / TikZ generation
# ---------------------------------------------------------------------------
 
def _coord_str(corners: List[Tuple[float, float]]) -> str:
    return " -- ".join(f"({x:.4f},{y:.4f})" for x, y in corners) + " -- cycle"
 

def _tikz_hex_lines(col: int, row: int, size: float, s: HexStyle, offset: Tuple[float,float]) -> List[str]:
    """Return the TikZ lines that draw one hexagon.

    The stroke path uses an inset circumradius so the border is drawn
    entirely inside the nominal hex boundary -- no bleed into neighbours.
    The fill/hatch path uses the full nominal size so the interior is
    completely covered with no visible gap.
    """
    cx, cy = hex_center(col, row, size, offset)

    # Full-size path for fill/hatch (covers the whole cell)
    cs_full  = _coord_str(hex_corners(cx, cy, size))
    # Inset path for the stroke (border stays inside the cell)
    r_inset  = inset_size(size, s["thickness"])
    cs_inset = _coord_str(hex_corners(cx, cy, r_inset))

    draw_opts = [s["thickness"], f"draw={s['color']}", "fill opacity=0.5", s["postaction"]]

    hatch = s.get("hatch", "")
    fill  = s.get("fill", "none")

    lines: List[str] = []

    if hatch:
        if fill != "none":
            lines.append(f"  \\fill[fill={fill}] {cs_full};")
        hatch_color = s.get("hatch_color") or s["color"]
        lines.append(f"  \\fill[pattern={hatch}, pattern color={hatch_color}] {cs_full};")
        # fill_for_draw = fill if fill != "none" else "white"
        # draw_opts.append(f"fill={fill_for_draw}")
        # The draw uses the inset path so the stroke sits inside;
        # re-apply fill on the inset shape so it covers up to the stroke edge
        # lines.append(f"  \\fill[fill={fill_for_draw}] {cs_inset};")
    else:
        draw_opts.append(f"fill={fill}")

    lines.append(f"  \\draw[{', '.join(draw_opts)}] {cs_inset};")
    return lines


def create_terrain_card(row, i):
    """populates the terrain including correct borders"""
    with open(terrianoutputfolder + str(i) + '.tex', 'w') as ofile:
        #load the background image
        terrain_text = "\\begin{tikzpicture}[backbox/.style= {rectangle, minimum height = 8.9cm," \
                + " minimum width =6.35cm, rounded corners = 0.3cm, fill=white, opacity=0.75}]\n "
        terrain_text += "\\node [rectangle, minimum width = 6.4cm, minimum height = 8.7cm, fill=black!70!white!30] at (3.25,4.5){};\n"
        terrain_text += '\\node at (3.25,4.45){\\includegraphics[width=6.5cm, max height = 8.9cm,' +\
                'keepaspectratio]{' + terrain_images_folder + row["BackgroundImg"] + '}};\n'

        # terrain card size
        cols = 4
        rows = 5
        hex_size = 0.865 #cm - radius

        hoffset = 0.2
        voffset = 0.6

        # superimpose the grid
        # put height/terrain information in where relevant (borders?)
        col_range = range(-1, cols + 1)
        row_range = range(-1, rows + 1)

        hex_lines: List[str] = []
        for c in col_range:
            for r in row_range:
                style = dict(DEFAULT_STYLE)
                if 0 <= c < cols and 0 <= r < rows:
                    style.update(STYLE_DICT.get(row[f"tile_{r}_{c}"], {}))
                hex_lines.append("\n".join(_tikz_hex_lines(c, r + 1, hex_size, style, (hoffset, voffset))))

        inner_body = "\n".join(hex_lines)

        # TODO - add this clipping to every card
        clip_line = f"  \\clip ({hoffset-0.05},{voffset-0.3}) rectangle (6.2, 8.6);"
        terrain_text += "\\begin{scope}\n" + clip_line + "\n" + inner_body + "\n" + "\\end{scope}\n"
        
        # add rules text if extant (probably an objective card)

        if row["Rules"]:
            terrain_text += "\\node[rectangle, fill = white, opacity = 0.75, minimum height =1.5cm, rounded corners = 0.3cm, " \
                    + "text width = 3.cm]  at (4.6, 1.7){\\small{" + row['Rules'] +"}};\n"


        # add objective information symbols
        if int(row["Points"]):
            terrain_text += '\\node at(1, 8.2){\\includegraphics[' + iconwidth + ']{' + icons_folder + pointsImg + '}};\n'
            terrain_text += "\\node at (1, 8.2){\\Large{\\textbf{" + row['Points'] +"}}};\n"
        if int(row["Tokens"]):
            terrain_text += '\\node at(2, 8.2){\\includegraphics[' + iconwidth + ']{' + icons_folder + tokensImg + '}};\n'
            terrain_text += "\\node at (2, 8.2){\\Large{\\textbf{" + row['Points'] +"}}};\n"


        #finish the tikzpicture
        terrain_text += "\\end{tikzpicture}\n"

        ofile.write(terrain_text)
        return terrain_text + "~"


#the actual run
if __name__ == "__main__":
    with open(cardoutputfolder + "all.tex", "w") as allfile:
        allfile.write(header_text)

        allfile.write(createMacros())

        allfile.write(begin_doc)

        i = 0
        with open(weapon_actions_file, "r") as spcsvfile:
            reader = csv.DictReader(spcsvfile)
            for row in reader:
                i = i + 1
                if int(row["Changed"]) > 0:
                        allfile.write(make_card_from_row(row, i, CardTypeEnum.WEAPON))

        with open(booster_actions_file, "r") as facsvfile:
            reader = csv.DictReader(facsvfile)
            for row in reader:
                i = i + 1
                if int(row["Changed"]) > 0:
                        allfile.write(make_card_from_row(row, i, CardTypeEnum.BOOSTER))

        with open(pilot_actions_file, "r") as facsvfile:
            reader = csv.DictReader(facsvfile)
            for row in reader:
                i = i + 1
                if int(row["Changed"]) > 0:
                        allfile.write(make_card_from_row(row, i, CardTypeEnum.PILOT))

        with open(general_action_file, "r") as gencsvfile:
            reader = csv.DictReader(gencsvfile)
            for row in reader:
                i = i + 1
                if int(row["Changed"]) > 0:
                    allfile.write(make_card_from_row(row, i, CardTypeEnum.BASIC))
        j = 0
        with open(frames_file, "r") as fcsvfile:
            reader = csv.DictReader(fcsvfile)
            allfile.write("\\newpage \n")
            for row in reader:
                j = j + 1
                if int(row["Changed"]) > 0:
                    allfile.write(create_frame_sheet(row, j))

        i = 0
        with open(terrain_file, "r") as tcsvfile:
            allfile.write("\\newpage \n")
            reader = csv.DictReader(tcsvfile)
            for row in reader:
                i += 1
                if int(row["Changed"]) > 0:
                    allfile.write(create_terrain_card(row, i))

        allfile.write("\\end{document}\n")
