#Reads the given CSV of card designs and turns them into TEX files that could be imported as needed

import csv
import enum
import math
import os
from typing import Any, Dict, List, Optional, Tuple, Union

weapon_actions_file = 'Weapon actions.csv'
general_action_file = 'Basic actions.csv'
pilot_actions_file = 'Pilot actions.csv'
drone_actions_file = 'Drone actions.csv'
booster_actions_file = 'Booster actions.csv'
terrain_file = "Terrain_square.csv"
frames_file = 'Frames.csv'

cardoutputfolder='build/card_'
frameoutputfolder='build/frame_'
backsoutputfolder='build/back_'
terrianoutputfolder='build/terrain_'
groupindicatoroutputfolder='build/group_indicator_'

#icon names
cutAtkImg = 'attackImg.png'
bulletAtkImg = 'rattackImg.png'
bludgeonAtkImg = "hammerAttackImg.png"
pierceAtkImg = "pierceAttackImg.png"
energyAtkImg = "energyAttackImg.png"

blkImg = 'blockImg.png'
rangeImg = 'rangeImg.png'
initImg = 'initImg.png'
mvImg = 'mvimg.png'

framemvImg = 'mvimg_old.png'
weaponImg = 'weapon.png'
boosterImg = 'boosterImg.png'
deckImg = 'deckImg.png'
atkpointsImg = 'atkpoints.png'
defpointsImg = 'defpoints.png'
tokensImg = 'token.png'


logos_dict = {
    "Aegis": "AegisLogo.png",
    "Collective": "CollectiveLogo.png",
    "Church of the Net": "CotNLogo.png",
    "Guild": "GuildLogo.png",
    "Ouwa": "OuwaLogo.png",
    "Revolution": "RevLogo.png"
}

light_logos_dict = {
    "Aegis": "AegisLogo_light.png",
    "Collective": "CollectiveLogo_light.png",
    "Church of the Net": "CotNLogo_light.png",
    "Guild": "GuildLogo_light.png",
    "Ouwa": "OuwaLogo_light.png",
    "Revolution": "RevLogo_light.png"
}

images_folder = "../pictures/"
terrain_images_folder = "../terrain/"
frame_images_folder = "../pictures/"
icons_folder = "../icons/"


frameImages = ["foreground/aegis_hector.png", "foreground/aegis_percival.png", "foreground/church_elemiah.png",
                    "foreground/church_hannael.png", "foreground/collective_adam.png", "foreground/collective_fenrir.png",
                    "foreground/guild_nautilus.png", "foreground/guild_salaryman.png", "foreground/ouwa_kamikiri.png",
                    "foreground/ouwa_kuwagata.png", "foreground/revolution_flamekin.png", "foreground/revolution_ripper.png"]

frameBackgrounds = ["proxy_background.png"] * len(frameImages)

iconwidth = "width=0.9cm"
inline_iconwidth = "width=0.4cm"
init_iconwidth = "width=1.35cm"
terrain_iconwidth_value = 0.6
terarin_iconwidth = f"width={terrain_iconwidth_value}cm"
logo_width = "width=1.2cm"

# tikzpicture [scale=...] used by make_card_from_row; offsets given in physical cm
# (e.g. move_icon_outline) need to be divided by this to land at the right size
card_scale = 0.86

# outline of mvImg (a right-pointing block arrow), as (dx, dy) cm offsets from its
# centre when drawn at iconwidth (0.9cm); used to draw a fill that lines up with it
move_icon_outline = [
    (-0.45, 0.234),
    (0.054, 0.234),
    (0.054, 0.414),
    (0.45, 0.0),
    (0.054, -0.414),
    (0.054, -0.234),
    (-0.45, -0.234),
]

header_text = "\\documentclass[a4paper, landscape]{article}\n \\usepackage[left =2cm, right = 2cm, " \
            + "top = 1.4cm, bottom =1.4cm]{geometry} \n \\usepackage{tikz} \n \\usepackage[export]{adjustbox}" \
            + "\n \\usetikzlibrary{positioning} \n \\usetikzlibrary{patterns} \n \\usetikzlibrary{calc} \n" + \
            "\\usepackage{contour}\n\\contourlength{0.8pt}\n"

begin_doc = "\\begin{document}\n\\noindent\n"

class CardTypeEnum(enum.Enum):
    BASIC = 0
    WEAPON = 1
    PILOT = 2
    BOOSTER = 3
    DRONE = 4

damage_type_dict = {
    "cut" : cutAtkImg,
    "pierce" : pierceAtkImg,
    "impact" : bludgeonAtkImg,
    "projectile" : bulletAtkImg,
    "energy" : energyAtkImg,
    # for macro
    "block": blkImg
    }

ability_dict = {
    "Guard Break": "This attack consumes one block per zone",
    "Feint": "This attack deals no damage",
    "Close Quarters": "Cannot be blocked by higher initiative attacks",
    "Committed": "this attack is discarded after resolving",
}

numbered_ability_dict = {
    "Knockback": "Move the target frame #1 steps in any direction away from the source"
}

status_dict = {
    "Stunned": ("-2 init", "stunned.png"),
    "Slowed": ("-2 mv", "slowed.png"),
    "Dazed": ("-2 card", "dazed.png"),
    "Stimmed": ("+2 init", "stimmed.png"),
    "Boosted": ("+2 mv", "boosted.png"),
    "Lucid": ("+2 cards", "lucid.png"),
    "Revealed": ("chosen actions are turned face up", "revealed.png")
}

rules_dict = {
    "drone": "A unit that attacks once each turn with this card, with its own movement and health."
}


def createMacros():
    with open(cardoutputfolder + 'macros.tex', 'w') as ofile:
        # Single source of truth for shared colours; card_all.tex embeds this
        # return value and rules.tex \inputs the generated card_macros.tex.
        card_text = "\\definecolor{cityblue}{RGB}{18,70,220}\n" \
                    "\\definecolor{citysteel}{RGB}{148,156,162}\n"

        for t, img in damage_type_dict.items():
            card_text += "\n\\newcommand{\\" + t + "}{"
            card_text += '\\includegraphics[' + iconwidth + ']{' + icons_folder + img + '}'
            card_text += "}\n\\newcommand{\\small" + t + "}{"
            card_text += '\\includegraphics[' + inline_iconwidth + ']{' + icons_folder + img + '}'
            card_text += "}\n"
            # repeated inline version: \smallcutx{N} draws N overlapping copies in a row,
            # using the same step/width ratio (0.5/0.9) as the attack icons in attack_box
            card_text += "\\newcommand{\\small" + t + "x}[1]{"
            card_text += '\\includegraphics[' + inline_iconwidth + ']{' + icons_folder + img + '}'
            card_text += "\\ifnum#1>1\n\\foreach \\d in {2,...,#1}{\\hspace{-0.178cm}"
            card_text += '\\includegraphics[' + inline_iconwidth + ']{' + icons_folder + img + '}}\n\\fi}\n'

        for ability, desc in ability_dict.items():
            cmd = ability.lower().replace(" ", "")
            card_text += "\n\\newcommand{\\" + cmd + "}{\\textbf{" + ability + "}}"
            card_text += "\n\\newcommand{\\full" + cmd + "}{\\textbf{" + ability
            card_text += "} \\emph{(" + desc + ")}}"

        for ability, desc in numbered_ability_dict.items():
            cmd = ability.lower().replace(" ", "")
            card_text += "\n\\newcommand{\\" + cmd + "}[1]{\\textbf{" + ability + " #1}}"
            card_text += "\n\\newcommand{\\full" + cmd + "}[1]{\\textbf{" + ability
            card_text += " #1} \\emph{(" + desc + ")}}"


        for status, (desc, img) in status_dict.items():
            cmd = status.lower().replace(" ", "")
            card_text += "\n\\newcommand{\\" + cmd + "}{\\textbf{" + status + "}"
            card_text += '\\includegraphics[' + inline_iconwidth + ']{' + icons_folder + img + '}}\n'
            card_text += "\n\\newcommand{\\full" + cmd + "}{\\textbf{" + status
            card_text += '}\\includegraphics[' + inline_iconwidth + ']{' + icons_folder + img + '}'
            card_text += " \\emph{(" + desc + ")}}\n"

        for rule, desc in rules_dict.items():
            card_text += "\n\\newcommand{\\" + rule + "text}{\\emph{(" + desc + ")}}"

        ofile.write(card_text)
        return card_text


def getTypeName(t: CardTypeEnum):
    return str(t).split(".")[-1].lower().capitalize()

def parse_int_safe(value: str) -> Optional[int]:
    """Parses an int from a CSV field, returning None for blank/non-numeric values."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def initiative_color(value: str) -> str:
    """Background circle color for the initiative marker - hue shifts blue→purple across initiative range, fixed brightness (matching old init 7)."""
    init_min, init_max = 1, 9
    parsed = parse_int_safe(value)
    if parsed is None:
        parsed = (init_min + init_max) // 2
    clamped = max(init_min, min(init_max, parsed))
    fraction = (clamped - init_min) / (init_max - init_min)
    # blue!100!magenta = pure blue (H=240°), blue!25!magenta ≈ purple (H=285°)
    blue_pct = round(100 - fraction * 75)
    return f"blue!{blue_pct}!magenta!20!white"

def movement_color(value: str) -> str:
    """Background circle color for the movement marker - red for negative movement, green for positive, tone scales with magnitude."""
    move_max_abs = 5
    parsed = parse_int_safe(value)
    if parsed is None or parsed == 0:
        return "white"
    fraction = min(abs(parsed), move_max_abs) / move_max_abs
    pct = round(20 + fraction * 70)
    color_name = "green" if parsed > 0 else "red"
    return f"{color_name}!{pct}!white"

def move_icon_outline_fill(pos: str, color: str) -> str:
    """Filled polygon matching the outline of mvImg, centred on pos (a TikZ coordinate string).

    move_icon_outline is given in physical cm to match iconwidth; pos is a coordinate
    in the enclosing tikzpicture, which is drawn at [scale=card_scale], so the offsets
    are scaled back up here to land at the correct physical size.
    """
    points = " -- ".join(
        f"($ {pos} + ({dx / card_scale},{dy / card_scale}) $)" for dx, dy in move_icon_outline
    )
    return "\\fill[" + color + "] " + points + " -- cycle;\n"

def block_shield_outline(pos, color):
    """Bold shield-shaped background+outline for a blocked zone, replacing
    the plain backbox rounded rectangle so a block reads as covering the
    whole attack, not just a pip icon.

    Footprint starts from the same 2cm square centred on (6.2, pos), but:
    - the top edge gets a shallow V notch at its centre
    - the bottom-left/right corners are raised 0.5cm
    - a point continues down from those raised corners into the dead space
      below the box (between this zone row and the next), which is where
      the (now centred) range indicator sits
    """
    x, y = 6.2, pos
    hw, hh = 1.0, 1.0
    notch_depth = 0.2
    tip_depth = 0.3
    corner_raise = 0.5
    tl = f"({x - hw}, {y + hh})"
    top = f"({x}, {y + hh - notch_depth})"
    tr = f"({x + hw}, {y + hh})"
    br = f"({x + hw}, {y - hh + corner_raise})"
    bl = f"({x - hw}, {y - hh + corner_raise})"
    tip = f"({x}, {y - hh - tip_depth})"
    return f"\\filldraw[draw={color}, line width=6pt, fill={color}!20, rounded corners=0.15cm] {tl} -- {top} -- {tr} -- {br} -- {tip} -- {bl} -- cycle;\n"

def attack_box(atk, rng, block, pos, dmg_type, color):
    out_text = ""

    # background: a plain rounded rectangle when there's no block; when
    # blocked, the shield shape itself (fill + bold outline) takes over as
    # the background, since the block applies to the whole attack in that
    # zone, not just the block pips
    if block:
        out_text = out_text + block_shield_outline(pos, color)
    elif atk:
        out_text = out_text + f"\\node[backbox, fill={color}!20] at (6.2, {pos}){{}};\n"
    # what graphic to use
    aimg = "\\" + dmg_type

    for d in range(0, atk):
        out_text = out_text + "\\node at (" + str(
            -(d / 2) + 7.0) + ', ' + str(pos + 0.5) + '){' + aimg + '};\n'

    # blocks -- the shield outline above is now the block indicator, so the
    # individual block pip icons are no longer drawn
    # for d in range(0, block):
    #     out_text = out_text + "\\node at (" + str(
    #         -(d / 2) + 7.0) + ', ' + str(pos - 0.5) + '){\\includegraphics[' + iconwidth + ']{' + icons_folder + \
    #                blkImg + '}};\n'
    # ranges
    if rng > 0:
        out_text = out_text + '\\node at ( 6.2, ' + str(pos - 0.55) + '){\\includegraphics[' + iconwidth + ']{' + \
                   icons_folder + rangeImg + '}};\n'
        out_text = out_text + '\\node at (6.2, ' + str(pos - 0.1) + '){\\Large{' + str(rng) + '}};\n'

    return out_text

def draw_armor(armor, position, penalty="", horizontal_pos = 7, hori_step=-0.7):
    # old style
#    rval = "\\node [rectangle, minimum width=2cm, minimum height = 1cm, fill = red, opacity = 0.75] at (6.5, "  + position + "){" + armor +"};\n"
    rval = ""
    # bars
    for i in range(armor):
        rval += "\\node [anchor=east, rectangle, rounded corners = 0.1cm, minimum width=0.9cm, minimum height=0.6cm, draw, fill=red, opacity=0.8, rotate=90] at "+\
            "(" + str(horizontal_pos) + ", " + str(position) + "){"
        if i == armor - 1:
            rval += "\\tiny{" + penalty + "}"
        rval += "};\n"
        horizontal_pos += hori_step

    return rval



# ---------------------------------------------------------------------------
# Per-weapon-group zone indicator (bottom-left of each weapon card)
#
# Shows, for the weapon *group* as a whole (not just this one card), which of
# the High/Mid/Low zones it can attack (red triangle) and/or block (blue
# square) across all its printed cards -- so a played card tips the opponent
# off about the rest of the kit, not just itself.
# ---------------------------------------------------------------------------
zones_order = ["High", "Mid", "Low"]  # top to bottom, matching card layout

def compute_group_zone_capabilities(rows):
    """rows: iterable of CSV dict-rows from Weapon actions.csv (already PrintID-filtered).
    Returns {group: {zone: {"attack": bool, "block": bool}}}."""
    caps: Dict[str, Dict[str, Dict[str, bool]]] = {}
    for row in rows:
        entry = caps.setdefault(row["Group"], {z: {"attack": False, "block": False} for z in zones_order})
        for z in zones_order:
            atk = parse_int_safe(row.get(f"{z}Attack"))
            if atk and atk > 0:
                entry[z]["attack"] = True
            blk = parse_int_safe(row.get(f"{z}Block"))
            if blk and blk > 0:
                entry[z]["block"] = True
    return caps

def _triangle_path(cx, cy, r=0.22):
    pts = [(cx, cy + r), (cx - r * 0.866, cy - r * 0.5), (cx + r * 0.866, cy - r * 0.5)]
    return " -- ".join(f"({x:.4f},{y:.4f})" for x, y in pts) + " -- cycle"

def _square_path(cx, cy, half=0.175):
    return f"({cx - half:.4f},{cy - half:.4f}) rectangle ({cx + half:.4f},{cy + half:.4f})"

# indicator is drawn at 2/3 of its original size so it reads as a compact
# corner marker rather than competing with the card's other icons
INDICATOR_SCALE = 2.0 / 3.0
INDICATOR_PAD = 0.06  # margin between icons and their background panel


def create_group_indicator(group, capability):
    """Writes the small standalone tikzpicture nested/\\input by each card of this group.

    Includes its own opaque-ish background panel (drawn first, underneath the
    icons) so the card's other UI boxes never show through behind it."""
    s = INDICATOR_SCALE
    zone_y = {"High": 1.25 * s, "Mid": 0.75 * s, "Low": 0.25 * s}
    atk_x, blk_x = 0.28 * s, 0.72 * s
    tri_r = 0.22 * s
    sq_half = 0.175 * s

    panel_left = atk_x - tri_r * 0.866 - INDICATOR_PAD
    panel_right = blk_x + sq_half + INDICATOR_PAD
    panel_bottom = zone_y["Low"] - max(tri_r * 0.5, sq_half) - INDICATOR_PAD
    panel_top = zone_y["High"] + max(tri_r, sq_half) + INDICATOR_PAD

    lines = ["\\begin{tikzpicture}[x=1cm,y=1cm]\n"]
    lines.append(
        f"  \\fill[white, opacity=0.75, rounded corners=0.05cm] "
        f"({panel_left:.4f},{panel_bottom:.4f}) rectangle ({panel_right:.4f},{panel_top:.4f});\n"
    )
    for zone in zones_order:
        y = zone_y[zone]
        tri = _triangle_path(atk_x, y, tri_r)
        if capability[zone]["attack"]:
            lines.append(f"  \\fill[red!80!black] {tri};\n")
        else:
            lines.append(f"  \\draw[gray!45, thin] {tri};\n")
        sq = _square_path(blk_x, y, sq_half)
        if capability[zone]["block"]:
            lines.append(f"  \\fill[blue!65!black] {sq};\n")
        else:
            lines.append(f"  \\draw[gray!45, thin] {sq};\n")
    lines.append("\\end{tikzpicture}\n")
    text = "".join(lines)
    with open(groupindicatoroutputfolder + group + ".tex", "w") as ofile:
        ofile.write(text)
    return text


def make_card_from_row(row, card_type, group_capability=None):
    with open(cardoutputfolder + row['Group'] + "_" + row['Name'] + '.tex', 'w') as ofile:
        # art and card edge
        card_text = f"\\begin{{tikzpicture}}[scale={card_scale}, backbox/.style= {{rectangle, minimum height=2.0cm," \
                   + " minimum width =2.0cm, rounded corners = 0.3cm, fill opacity=0.75}]\n "
        card_text = card_text + "\\node [rectangle, minimum width = 6.2cm, minimum height = 8.5cm, fill=black] at (4,5){};\n"
        # background
        if row.get("BackgroundLayer"):
            card_text = card_text + '\\node at (4,5){\\includegraphics[width=6cm, max height = 8.3cm,' +\
                  ' keepaspectratio]{' + images_folder + row["BackgroundLayer"] + '}};\n'
        card_text = card_text + '\\node at (4,5){\\includegraphics[width=6cm, max height = 8.3cm,' +\
              ' keepaspectratio]{' + images_folder + row["CardImg"] + '}};\n'
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
        if card_type is CardTypeEnum.DRONE:
            # TODO - create drone frame
            card_text += draw_armor(int(row["Drone_Health"]), 7.1, horizontal_pos=1.4, hori_step=0.7)
            drone_mv_pos = "(1.7, 5.5)"
            card_text += '\\node at ' + drone_mv_pos + '{\\includegraphics[' + iconwidth + ']{' + icons_folder + framemvImg + '}};\n'
            card_text += " \\node at " + drone_mv_pos + "{\\Large{\\textbf{" + row['Drone_MV'] +"}}};\n"

            pass
        
        init_pos = "(1.2, 9.0)"
        move_pos = "(1, 7.7)"

        # background circles for the initiative and movement markers, drawn before the name plate
        # so it sits on top of any overlap
        card_text = card_text + "\\node[circle, fill=" + initiative_color(row['Initiative']) + ", minimum size=1.5cm] at " + init_pos + "{};\n"
        card_text = card_text + move_icon_outline_fill(move_pos, movement_color(row['Movement']))

        # default symbols
        card_text = card_text + '\\node at ' + init_pos + '{\\includegraphics[' + init_iconwidth + ']{' + icons_folder + initImg + '}};\n'
        card_text = card_text + "\\node at " + init_pos + "{\\huge{\\textbf{\\contour{white}{" + row['Initiative'] +"}}}};\n"
        card_text = card_text + '\\node at ' + move_pos + '{\\includegraphics[' + iconwidth + ']{' + icons_folder + mvImg + '}};\n'
        card_text = card_text + " \\node at " + move_pos + "{\\Large{\\textbf{" + row['Movement'] +"}}};\n"

        # name and faction
        card_text = card_text + "\\node [rectangle, minimum width=4cm, minimum height = 0.6cm,rounded corners = 0.1cm," +\
                "fill=white, opacity=0.75, text width=4cm] at (4.3, 9.2){\\large{" + row["Name"]
        if row["Faction"]:
            card_text = card_text +  "}\\\\\n\\small{\\emph{~" + row["Faction"] + "}"
        card_text = card_text +  "}};\n"

        if row["Faction"]:
            card_text += "\\node[opacity=0.7] at (2.2, 7.9) {\\includegraphics[" + logo_width + "]{" + images_folder + light_logos_dict[row["Faction"]] + "}};\n"


        if row["Persistence"] != "0":
             card_text = card_text + "\\node at (7,9.2)[circle, fill = red]{\\large{\\textbf{$" + row["Persistence"] + "$}}};\n"

        try:
            if card_type is CardTypeEnum.PILOT:
                card_text = card_text + attack_box(0, 0, 1, 7.5, "", "yellow")
            else:
                card_text = card_text + attack_box(int(row["HighAttack"]), int(row["HighRange"]), int(row["HighBlock"]), 7.5, row["HighDType"], "yellow")
                card_text = card_text + attack_box(int(row["MidAttack"]), int(row["MidRange"]), int(row["MidBlock"]), 5.0, row["MidDType"], "red")
                card_text = card_text + attack_box(int(row["LowAttack"]), int(row["LowRange"]), int(row["LowBlock"]), 2.5, row["LowDType"], "blue")
        except:
            print(f"exception for {row["Group"]} {row['Name']}")
            return ""

        # textbox
        if card_type is CardTypeEnum.PILOT:
            card_text = card_text + "\\node[rectangle, fill = white, opacity = 0.75, minimum height =2.5cm, rounded corners = 0.1cm, " \
                    + "text width = 5.4cm]  at (4, 3.5){\\small{" + row['Text'] +"}};\n"
        else:
            if row["Text"]:
                card_text = card_text + "\\node[rectangle, fill = white, opacity = 0.75, minimum height =1.5cm, rounded corners = 0.1cm, " \
                    + "text width = 3.5cm]  at (2.75, 3.5){\\small{" + row['Text'] +"}};\n"

        # set info: deckbuilding info (Faction/Type/Group/Flavor) is narrower and
        # shifted right, and its font shrunk, for weapon cards to leave room for
        # the group zone-capability indicator down in the bottom-left corner
        set_info_content = row["Faction"] + " " + getTypeName(card_type) +  " \\hfill " + row['Group'] + "\\\\" + \
                "\\footnotesize{\\emph{" + row["Flavor"] + "}}"
        if group_capability is not None:
            set_info_center_x, set_info_width = 4.45, 4.9
            set_info_content = "\\footnotesize{" + set_info_content + "}"
        else:
            set_info_center_x, set_info_width = 4, 5.8
        card_text = card_text + f"\\node[rectangle, fill = white, opacity = 0.75, minimum width={set_info_width}cm, minimum height =0.8cm, " \
                + f"rounded corners = 0.1cm, text width = {set_info_width}cm]  at ({set_info_center_x}, 0.7){{" \
                + set_info_content + "};\n"

        # group zone-capability indicator: which zones this weapon group can
        # attack/block across all its cards, not just this one -- pinned to the
        # true bottom-left corner of the card
        if group_capability is not None:
            card_text = card_text + "\\node[anchor=south west, inner sep=0pt] at (0.55, 0.25){\\input{" \
                    + "../build/group_indicator_" + row['Group'] + ".tex}};\n"

        # Foreground overlay rendered above all other elements (optional; use PNG for transparency)
        if row.get("ForegroundImg"):
            card_text = card_text + '\\node at (4,5){\\includegraphics[width=6cm, max height = 8.3cm,' +\
                  ' keepaspectratio]{' + images_folder + row["ForegroundImg"] + '}};\n'

        # artist credit -- kept aligned under the (possibly shifted) set info box
        if row.get("Artist"):
            card_text = card_text + f"\\node at ({set_info_center_x}, 0.3){{\\footnotesize{{\\copyright  LiliCo 2026 \\emph{{ Art: " + row["Artist"] + "}}};\n"

        card_text = card_text + "\\end{tikzpicture}\n"
        # ofile.write(header_text)
        ofile.write(card_text)
        # ofile.write("\\end{document}\n")
        return card_text + "~"
    
def create_frame_sheet(frame):
    """creates the frames datasheet procedurally from the given data"""
    with open(frameoutputfolder + frame["Name"] + '.tex', 'w') as ofile:
        #load the initial image
        frame_text = "\\begin{tikzpicture}[scale=0.86, backbox/.style= {rectangle, minimum height = 2.2cm," \
                + " minimum width =2.2cm, rounded corners = 0.3cm, fill=white, opacity=0.75}]\n "
        frame_text = frame_text + "\\node [rectangle, minimum width = 6.2cm, minimum height = 8.5cm, fill=black!70!white!30] at (4,5){};\n"
        # background
        if frame.get("BackgroundLayer"):
            frame_text = frame_text + '\\node at (4,5){\\includegraphics[width=6cm, max height = 8.3cm, keepaspectratio]{' + frame_images_folder + frame["BackgroundLayer"] + '}};\n'
        frame_text = frame_text + '\\node at (4,5){\\includegraphics[width=6cm, max height = 8.3cm, keepaspectratio]{' + frame_images_folder + frame["CardImg"] + '}};\n'
        # name
        frame_text = frame_text + "\\node [rectangle, minimum width=4.3cm, minimum height = 1cm,rounded corners = 0.1cm, fill=white, opacity=0.75, text width=4.1cm]" +\
                            "at (3.3, 9){\\large{" + frame["Name"] + "}\\\\\n\\small{\\emph{~" + frame["Faction"] + "}}};\n"
        
        # movement
        frame_text = frame_text + '\\node at (7,9){\\includegraphics[' + iconwidth + ']{' + icons_folder + framemvImg + '}};\n'
        frame_text = frame_text + " \\node at (7,9){\\Large{\\textbf{" + frame['Movement'] +"}}};\n"
        
        if frame["Faction"]:
            frame_text += "\\node[opacity=0.7] at (1.5, 7.7) {\\includegraphics[" + logo_width + "]{" + images_folder + light_logos_dict[frame["Faction"]] + "}};\n"

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
                '} ~\\includegraphics[' + inline_iconwidth + ']{' + icons_folder + deckImg + '}\\large{  : ' + str(frame["Deck size"]) + \
                 "} \\\\\\emph{\\footnotesize{" + frame["Flavor"] +  "}}};\n"

        # Foreground overlay rendered above all other elements (optional; use PNG for transparency)
        if frame.get("ForegroundImg"):
            frame_text = frame_text + '\\node at (4,5){\\includegraphics[width=6cm, max height = 8.3cm, keepaspectratio]{' + frame_images_folder + frame["ForegroundImg"] + '}};\n'

        # artist credit
        if frame.get("Artist"):
            frame_text = frame_text + "\\node at (4, 0.3){\\footnotesize{\\copyright LiliCo 2026 \\emph{Art: " + frame["Artist"] + "}}};\n"

        #finish the tikzpicture
        frame_text = frame_text + "\\end{tikzpicture}\n"

        ofile.write(frame_text)
        return frame_text + "~"


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
        terrain_text += "\\node [rectangle, minimum width = 6.4cm, minimum height = 8.7cm, fill=black!70!white!30] at (3.25,4.5){};\n"
        terrain_text += '\\node at (3.25,4.45){\\includegraphics[width=6.35cm, max height = 8.85cm,' +\
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


def create_flipped(frame):
    output = "\\begin{tikzpicture}[baseline=(a.north)]\n"
    #upside down node
    output += "\\node[yscale=-1,inner sep=0,outer sep=0](a){\\includegraphics[height=3cm, max width = 2.5cm, keepaspectratio]{" 
    output += frame_images_folder + frame + '}};\n'
    
    # normal node
    output += "\\node[inner sep=0,outer sep=0, anchor=south] at (a.south) {\\includegraphics[height=3cm, max width = 2.5cm, keepaspectratio]{" 
    output += frame_images_folder + frame + '}};\n'

    #end the tikz
    output += '\\end{tikzpicture}\n~'
    return output

def create_back(frame, background):
    """creates the frames card/sleeve back"""
    with open(backsoutputfolder + os.path.basename(frame).split(".")[0] + '.tex', 'w') as ofile:
        #load the initial image
        frame_text = "\\begin{tikzpicture}[scale=0.86, backbox/.style= {rectangle, minimum height = 2.2cm," \
                + " minimum width =2.2cm, rounded corners = 0.3cm, fill opacity=0.75}]\n "
        frame_text = frame_text + "\\node [rectangle, minimum width = 6.2cm, minimum height = 8.5cm, fill=black!70!white!30] at (4,5){};\n"
        # background
        frame_text = frame_text + '\\node at (4,5){\\includegraphics[width=6cm, max height = 8.3cm, keepaspectratio]{' + frame_images_folder + background + '}};\n'
        frame_text = frame_text + '\\node at (4,5){\\includegraphics[width=6cm, max height = 8.3cm, keepaspectratio]{' + frame_images_folder + frame + '}};\n'
        
        #finish the tikzpicture
        frame_text = frame_text + "\\end{tikzpicture}\n"

        ofile.write(frame_text)
        return frame_text + "~"

#the actual run
if __name__ == "__main__":
    with open(cardoutputfolder + "all.tex", "w") as allfile:
        allfile.write(header_text)

        allfile.write(createMacros())

        allfile.write(begin_doc)

        with open(booster_actions_file, "r") as facsvfile:
            reader = csv.DictReader(facsvfile)
            for row in reader:
                for printcount in range(int(row["PrintID"])):
                        allfile.write(make_card_from_row(row, CardTypeEnum.BOOSTER))

        with open(weapon_actions_file, "r") as spcsvfile:
            reader = csv.DictReader(spcsvfile)
            weapon_rows = [row for row in reader if int(row["PrintID"]) > 0]

        weapon_group_caps = compute_group_zone_capabilities(weapon_rows)
        for group, capability in weapon_group_caps.items():
            create_group_indicator(group, capability)

        for row in weapon_rows:
            allfile.write(make_card_from_row(row, CardTypeEnum.WEAPON, weapon_group_caps[row["Group"]]))

        with open(drone_actions_file, "r") as facsvfile:
            reader = csv.DictReader(facsvfile)
            for row in reader:
                if int(row["PrintID"]) > 0:
                        allfile.write(make_card_from_row(row, CardTypeEnum.DRONE))

        with open(pilot_actions_file, "r") as facsvfile:
            reader = csv.DictReader(facsvfile)
            for row in reader:
                if int(row["PrintID"]) > 0:
                        allfile.write(make_card_from_row(row, CardTypeEnum.PILOT))

        with open(general_action_file, "r") as gencsvfile:
            reader = csv.DictReader(gencsvfile)
            for row in reader:
                if int(row["PrintID"]) > 0:
                    allfile.write(make_card_from_row(row, CardTypeEnum.BASIC))
        with open(frames_file, "r") as fcsvfile:
            reader = csv.DictReader(fcsvfile)
            allfile.write("\\newpage \n\\noindent\n")
            for row in reader:
                if int(row["PrintID"]) > 0:
                    allfile.write(create_frame_sheet(row))
        # terrain doesnt have names yet
        with open(terrain_file, "r") as tcsvfile:
            allfile.write("\\newpage \n\\noindent ")
            reader = csv.DictReader(tcsvfile)
            for row in reader:
                if int(row["PrintID"]) > 0:
                    allfile.write(create_terrain_card(row))

        # standees
        allfile.write("\\newpage \n\\noindent ")
        for frame in  frameImages:
            allfile.write(create_flipped(frame))
        # card backs
        allfile.write("\\newpage \n\\noindent ")
        for frame, background in  zip(frameImages, frameBackgrounds):
            allfile.write(create_back(frame, background))

        allfile.write("\\end{document}\n")
