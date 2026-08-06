#Reads the given CSV of card designs and turns them into TEX files that could be imported as needed

import csv
import enum
import math
import os
from typing import Any, Dict, List, Optional, Tuple, Union

from terrain_cards import terrain_file, terrianoutputfolder, create_terrain_card

weapon_actions_file = 'Weapon actions.csv'
general_action_file = 'Basic actions.csv'
pilot_actions_file = 'Pilot actions.csv'
drone_actions_file = 'Drone actions.csv'
booster_actions_file = 'Booster actions.csv'
frames_file = 'Frames.csv'

buildfolder='build/'
# Per-card, frame and terrain tiles live in their own subfolders
# (build/card/<Group>_<Name>.tex, build/frame/<Name>.tex, build/terrain/<Name>.tex)
# rather than using card_/frame_/terrain_ filename prefixes.
cardoutputfolder=buildfolder+'card/'
frameoutputfolder=buildfolder+'frame/'
backsoutputfolder=buildfolder+'back_'
groupindicatoroutputfolder=buildfolder+'group_indicator_'

# Create output folders up front so writes never fail on a fresh/cleared build/.
for _folder in (buildfolder, cardoutputfolder, frameoutputfolder, terrianoutputfolder):
    os.makedirs(_folder, exist_ok=True)

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
logo_width = "width=1.2cm"

# Coordinate unit (in cm) for the card/frame tikzpictures. We set the picture's
# x/y unit vectors to this rather than using [scale=...]: a `scale` key applies a
# pgf canvas transform that is re-evaluated when the picture is nested inside
# another box/node, which makes the `max height` background \includegraphics
# recompute at a different size and shifts the whole layout. Setting x=/y= fixes
# the units at typeset time, so the card renders identically whether standalone,
# \resizebox'd, or nested -- the geometry is exactly the same as scale=card_scale.
# Offsets given in physical cm (e.g. move_icon_outline) are divided by this to
# land at the right size, just as before.
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

# ---------------------------------------------------------------------------
# New card layout (2026 redesign)
#
# Coordinates are in tikzpicture units (x=y=card_scale cm). The card base
# rectangle is centred at (4,5), 6.2cm x 8.5cm, so the usable field is roughly
# x in [0.5, 7.5], y in [0.3, 9.3]. The layout is a top name row, a right-hand
# column of three always-drawn zone boxes, a constrained art zone on the left,
# and a full-width ability/rules box across the bottom.
# ---------------------------------------------------------------------------

# A/B flag: sharp box corners by default; flip to True to restore rounded ones.
ROUNDED_CORNERS = False

def rc():
    """Corner-radius string for the current ROUNDED_CORNERS setting."""
    return "0.12cm" if ROUNDED_CORNERS else "0pt"

# Flat card background (the whole-card artwork background is gone; art is boxed).
CARD_BG = "black!42!white"

# Art zone (left). minimum width/height are physical cm; the centre is in tikz
# units. The zone column is aligned to the art's vertical extent, so ART_TOP /
# ART_BOT (tikz units) are derived here and reused for both. The card base is
# 6.2cm wide; art/rules widths and positions leave a small border inside it.
ART_CX, ART_CY = 2.81, 5.9
ART_W_CM, ART_H_CM = 3.85, 4.2
ART_TOP = ART_CY + (ART_H_CM / 2) / card_scale
ART_BOT = ART_CY - (ART_H_CM / 2) / card_scale

# Full-width bottom rules box: fixed width (inside the card border) and a fixed
# bottom edge (tikz units); it grows upward by card type. Set info + copyright
# sit in the compact band below it.
RULES_W_CM = 5.9
RULES_BOTTOM = 0.7

# Name plate / initiative / movement (top row). Init and movement sit in the
# top corners; the name plate spans from the init-circle centre to the
# movement-chevron centre. NAME_H_CM also sizes the init circle and chevron.
# Fixed height so a one- or two-line (faction) plate keeps its top a hair below
# the card edge; the init circle and chevron are sized to match this height.
NAME_CY = 9.26
NAME_H_CM = 1.1
INIT_POS = (1.15, NAME_CY)
MOVE_POS = (6.58, NAME_CY)
# half-length of the movement chevron (50% longer than the default chevron); the
# centre above is chosen so the lengthened tip still lands just inside the corner
MOVE_CHEVRON_W = 0.93
# chevron half-height in tikz units so it matches the name-plate height
CHEVRON_HALF_H = (NAME_H_CM / 2) / card_scale

# Zone boxes (right column). Physical sizes in cm; centres in tikz units.
# Nudged left of the art centre so the wider super-block marker (see
# \superblock in card_macros.tex) does not spill past the card's right edge.
ZONE_CX = 6.12
ZONE_W_CM = 1.65
ZONE_H_CM = 1.35
# Half extents expressed in tikz units (physical cm / card_scale) for drawing
# shields / positioning icons relative to a box centre.
ZONE_HALF_W = (ZONE_W_CM / 2) / card_scale
ZONE_HALF_H = (ZONE_H_CM / 2) / card_scale
# The three zone boxes fill the art's vertical span: the High box's top edge
# lines up with the top of the art, the Low box's bottom with the bottom.
ZONE_CY = {"High": ART_TOP - ZONE_HALF_H, "Low": ART_BOT + ZONE_HALF_H}
ZONE_CY["Mid"] = (ZONE_CY["High"] + ZONE_CY["Low"]) / 2

# Frame armour bars / loadout boxes reuse the zone *heights* (vertical alignment
# with the attack-card zones is what matters) but not the leftward ZONE_CX nudge:
# frames have no super-block markers, so that extra right-edge clearance isn't
# needed. Give them their own X centre whose gap to the card edge is half the
# (wider) zone gap, so they sit further right without spilling.
CARD_RIGHT = 4 + (6.2 / card_scale) / 2
FRAME_ZONE_CX = CARD_RIGHT - ZONE_HALF_W - (CARD_RIGHT - (ZONE_CX + ZONE_HALF_W)) / 2
# Frame ability box (bottom-left) + loadout column geometry. The full-card art
# region, the three loadout boxes and the ability box all reference these so the
# loadout column lines up vertically with the ability box.
ABIL_CX, ABIL_W_CM, ABIL_H_CM = 2.86, 4.1, 2.0
ABIL_BOTTOM_Y = 1.05
ABIL_TOP_Y = ABIL_BOTTOM_Y + ABIL_H_CM / card_scale

# Pilot cards show a High block and normal Mid box, but a half-height Low box so
# the rules box can rise higher into the freed space.
PILOT_LOW_H_CM = ZONE_H_CM / 2
# top edge kept where the normal Low box's top is; box shrinks downward from there
PILOT_LOW_CY = (ZONE_CY["Low"] + ZONE_HALF_H) - (PILOT_LOW_H_CM / 2) / card_scale

header_text = "\\documentclass[a4paper, landscape]{article}\n \\usepackage[left =2cm, right = 2cm, " \
            + "top = 1.4cm, bottom =1.4cm]{geometry} \n \\usepackage{tikz} \n \\usepackage[export]{adjustbox}" \
            + "\n \\usetikzlibrary{positioning} \n \\usetikzlibrary{patterns} \n \\usetikzlibrary{calc} \n" + \
            "\\usepackage[none]{hyphenat} \n\\usepackage{contour}\n\\contourlength{0.8pt}\n"

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
    "Reload": "This frames next action from this weapon deals no damage.",
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
    with open(buildfolder + 'card_macros.tex', 'w') as ofile:
        card_text = "\\definecolor{cityblue}{RGB}{105,156,255}\n" \
                    "\\definecolor{citysteel}{RGB}{78,76,118}\n"
        # The base shield is split into a fill and a stroke so the super-block
        # variant can slip its inner emphasis line *between* them (on top of the
        # fill, under the middle outline) -- otherwise the opaque fill would
        # paint over the inner line. \blockshield composes both for normal use.
        # Shield-shaped block marker. The path is parameterised by centre
        # (#1,#2) and half-width/half-height (#3,#4) so it can be fitted to the
        # new fixed-size zone boxes (a top V-notch and a pointed bottom, kept
        # inside the box). fill/line/blockshield take an extra colour arg (#3);
        # the super-block variant is composed in Python (block_shield_outline)
        # from these plus two concentric outline strokes.
        card_text += (
            # Two distinct block markers, both parameterised by centre (#1,#2)
            # and half-width/half-height (#3,#4) so they fit any zone box.
            #  * normal block: horizontal top, vertical sides, pointed bottom.
            #  * super block : a notched shield (top V-notch, inward sides,
            #                  pointed bottom) plus a concentric inner line.
            # They share the pointed-bottom family but read very differently.
            "\n\\newcommand{\\normalblockpath}[4]{"
            "(#1-#3,#2+#4) -- (#1+#3,#2+#4) -- (#1+#3,#2-0.35*#4) -- "
            "(#1,#2-#4) -- (#1-#3,#2-0.35*#4) -- cycle}\n"
            "\\newcommand{\\superblockpath}[4]{"
            "(#1-#3,#2+#4) -- (#1,#2+0.66*#4) -- (#1+#3,#2+#4) -- "
            "(#1+0.8*#3,#2-0.35*#4) -- (#1,#2-#4) -- (#1-0.8*#3,#2-0.35*#4) -- cycle}\n"
            # A thick black outline is stroked first so it sits *behind* the
            # fill and the coloured outline, leaving a clean black rim.
            "\\newcommand{\\normalblock}[5]{"
            "\\draw[draw=black, line width=7pt, line join=round, rounded corners=0.06cm] \\normalblockpath{#1}{#2}{#4}{#5};"
            "\\fill[#3!25, fill opacity=0.6, rounded corners=0.06cm] \\normalblockpath{#1}{#2}{#4}{#5};"
            "\\draw[draw=#3, line width=4.5pt, rounded corners=0.06cm] \\normalblockpath{#1}{#2}{#4}{#5};}\n"
            # The super block is drawn a little wider than a normal block and
            # its inner emphasis line is thickened so it clearly stands apart.
            "\\newcommand{\\superblock}[5]{"
            "\\pgfmathsetmacro{\\sbw}{#4*1.3}"
            "\\draw[draw=black, line width=7pt, line join=round, rounded corners=0.08cm] \\superblockpath{#1}{#2}{\\sbw}{#5};"
            "\\fill[#3!25, fill opacity=0.6, rounded corners=0.08cm] \\superblockpath{#1}{#2}{\\sbw}{#5};"
            "\\draw[draw=#3, line width=4.5pt, rounded corners=0.1cm] \\superblockpath{#1}{#2}{\\sbw}{#5};"
            "\\pgfmathsetmacro{\\sbiw}{\\sbw-0.22}\\pgfmathsetmacro{\\sbih}{#5-0.22}"
            "\\draw[draw=#3!55!black, line width=3.5pt, rounded corners=0.08cm] \\superblockpath{#1}{#2}{\\sbiw}{\\sbih};}\n"
            # Fixed-size 3-arg wrappers (centre + colour) so the rules document
            # draws its block markers from exactly the same shapes as the cards.
            "\\newcommand{\\blockshield}[3]{\\normalblock{#1}{#2}{#3}{0.9}{1.0}}\n"
            "\\newcommand{\\superblockshield}[3]{\\superblock{#1}{#2}{#3}{0.9}{1.0}}\n"
        )

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
    if parsed is None:
        return "white"
    if parsed == 0:
        # yellow keeps a 0 chevron visible against the pale card/name plate
        return "yellow"
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

def draw_chevron(cx, cy, color, text, w=0.62, h=0.44, point=0.28, fontsize="\\large", name=None):
    """A right-pointing chevron/pennant marker (flat back with a concave left
    notch, pointed right) filled with ``color`` and carrying ``text``. Replaces
    the old loaded-arrow movement graphic; also reused for a drone's own
    movement. ``name`` optionally emits a centre coordinate for rules callouts."""
    pts = [
        (cx - w, cy + h), (cx + w - point, cy + h), (cx + w, cy),
        (cx + w - point, cy - h), (cx - w, cy - h), (cx - w + point, cy),
    ]
    path = " -- ".join(f"({x:.3f},{y:.3f})" for x, y in pts)
    out = f"\\fill[{color}, draw=black, ultra thick] {path} -- cycle;\n"
    out += f"\\node at ({cx - 0.09 * (point):.3f},{cy:.3f}){{{fontsize}\\textbf{{{text}}}}};\n"
    if name:
        # place the callout coordinate at the right-hand tip (the edge nearest the
        # card's right edge, where the movement callout sits)
        out += f"\\coordinate ({name}) at ({cx + w:.3f},{cy:.3f});\n"
    return out


def draw_health_bars(n, cx, cy, half_h, name=None):
    """A row of ``n`` red bars (styled like the frame armour bars) for a drone's
    hit points, filled left-to-right from ``cx``. ``half_h`` sets the bar height,
    matched to the drone movement chevron."""
    bar_w = 0.28
    pitch = 0.42
    out = ""
    for i in range(n):
        x = cx + i * pitch
        out += (f"\\draw[fill=red, draw=black, line width=0.5pt, rounded corners=0.06cm] "
                f"({x:.3f},{cy - half_h:.3f}) rectangle ({x + bar_w:.3f},{cy + half_h:.3f});\n")
    if name:
        # left edge of the first bar (the callout for drone health is on the left)
        out += f"\\coordinate ({name}) at ({cx:.3f},{cy:.3f});\n"
    return out


# Frame armour bars: tall, thin red pips filling a zone box left-to-right. Four
# sit comfortably across the zone width; a fifth spills past the right edge on
# purpose. Sizes are in tikz units (the zone box is ZONE_W_CM/ZONE_H_CM cm).
ARMOR_BAR_PITCH = 0.44
ARMOR_BAR_W = 0.30
ARMOR_BAR_HALF_H = 0.62

def draw_armor_bars(count, cx, cy, penalty="", name=None):
    """Vertical red armour bars for a frame zone centred at (cx, cy). They are
    right-justified (filled in from the zone's right edge leftwards) so a fifth
    bar spills left into the art. The penalty label (e.g. -1Init) sits rotated
    inside the last, leftmost bar, as the old datasheet showed it."""
    right0 = cx + ZONE_HALF_W - 0.12  # right edge of the rightmost (first) bar
    out = ""
    last_cx = None
    for i in range(count):
        xr = right0 - i * ARMOR_BAR_PITCH
        xl = xr - ARMOR_BAR_W
        out += (f"\\draw[fill=red, draw=black, line width=0.5pt, rounded corners=0.06cm] "
                f"({xl:.3f},{cy - ARMOR_BAR_HALF_H:.3f}) rectangle "
                f"({xr:.3f},{cy + ARMOR_BAR_HALF_H:.3f});\n")
        last_cx = (xl + xr) / 2
    if penalty and last_cx is not None:
        out += (f"\\node[rotate=90, font=\\tiny\\bfseries, text=white] "
                f"at ({last_cx:.3f},{cy:.3f}){{{penalty}}};\n")
    if name:
        # right edge of the zone (the armour callouts sit in the right gutter)
        out += f"\\coordinate ({name}) at ({cx + ZONE_HALF_W:.3f},{cy:.3f});\n"
    return out


def estimated_text_len(text):
    """Rough rendered length of a card's text, accounting for ``\\full...`` ability
    macros that expand to a much longer description. Used to pick a smaller font
    for unusually busy weapon/drone cards whose bottom box is height-capped."""
    length = len(text)
    length += 45 * text.count("\\full")
    return length


def block_shield_outline(cx, cy, color, half_w, half_h, super_block=False):
    """Background+outline for a blocked zone, drawn in place of the plain zone
    rectangle so a block reads as covering the whole zone. A normal block uses
    the flat-topped \\normalblock marker; a super block (a block value greater
    than 1) uses the notched \\superblock shield, which is visibly distinct.

    Both markers live in card_macros.tex (\\normalblock / \\superblock, built on
    \\normalblockpath / \\superblockpath) so the cards and the rules reference
    draw from exactly the same shapes. Each is fitted to the zone box: centre
    (cx,cy), half-width/half-height in tikz units, all kept inside the box."""
    macro = "\\superblock" if super_block else "\\normalblock"
    return f"{macro}{{{cx}}}{{{cy}}}{{{color}}}{{{half_w:.3f}}}{{{half_h:.3f}}}\n"

def attack_box(atk, rng, block, cy, dmg_type, color, cx=ZONE_CX,
               half_w=ZONE_HALF_W, half_h=ZONE_HALF_H):
    """Draw one always-present zone box centred at (cx, cy).

    The box outline is a plain rectangle, or the shield shape when the zone is
    blocked (doubled for a super block). Attack icons sit at the vertical middle
    of the box filling in from the right; the range indicator (a 90-degree
    rotated icon plus its number) sits at the left of the box."""
    out_text = ""
    # an empty zone (no attack and no block) gets no box drawn at all
    if not atk and not block:
        return out_text
    w_cm = half_w * 2 * card_scale
    h_cm = half_h * 2 * card_scale

    if block:
        out_text += block_shield_outline(cx, cy, color, half_w, half_h*0.9, super_block=block > 1)
    else:
        fill = f"{color}!20" if atk else "black!10!white"
        out_text += (f"\\node[rectangle, draw=black!45, fill={fill}, fill opacity=0.6, rounded corners={rc()}, "
                     f"minimum width={w_cm:.3f}cm, minimum height={h_cm:.3f}cm] at ({cx}, {cy}){{}};\n")

    # attack icons: vertical middle, filling in from the right edge leftwards,
    # at 70% size and packed tightly
    aimg = "\\scalebox{0.7}{\\" + dmg_type + "}" if dmg_type else ""
    start_x = cx + half_w - 0.34
    for d in range(0, atk):
        out_text += f"\\node[inner sep=0pt] at ({start_x - d * 0.34:.3f}, {cy}){{{aimg}}};\n"

    # range: the number at the centre-left of the box, the 90-degree rotated
    # range icon just to its right, both on the box's vertical centre line
    if rng > 0:
        num_x = cx - half_w + 0.3
        icon_x = num_x + 0.42
        out_text += f'\\node at ({num_x:.3f}, {cy}){{\\large{{\\textbf{{{rng}}}}}}};\n'
        out_text += (f'\\node at ({icon_x:.3f}, {cy}){{\\includegraphics[angle=90,{iconwidth}]{{'
                     + icons_folder + rangeImg + '}};\n')

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


# ---------------------------------------------------------------------------
# Rules annotation
#
# Each card/frame element is given a TikZ node name (e.g. (initbox), (nameplate))
# so a separate rules diagram can point callouts at it. Naming a node changes
# nothing in the rendered card. When a card is built with annotate=True the
# callouts below are appended inside the same tikzpicture: a short labelled text
# box in the left/right gutter with a thin leader line to the named element.
# ---------------------------------------------------------------------------

def _aim_node(aim):
    """Return the node name an aim string points at, or None if it is a bare
    coordinate like ``(6.2,7.5)`` (which is always available)."""
    inner = aim.strip("()")
    if inner and (inner[0].isalpha() or inner[0] == "_"):
        return inner.split(".")[0]
    return None

# Vertical layout of the callout labels. Labels are stacked down each gutter
# with the minimum gap each pair actually needs to avoid overlapping (based on
# their rendered text height) but are otherwise pulled as close as possible to
# their target's real height, so the leader line stays close to horizontal. A
# side with many/clustered labels still spills above and below the card edges
# rather than cramming, since the minimum gap alone can force that; a side
# with few/spread-out labels follows its targets instead of bunching at a
# fixed centre.
CALLOUT_TITLE_LINE_H = 0.56   # cm per (always single-line) title
CALLOUT_DESC_LINE_H = 0.44    # cm per wrapped line of the smaller desc text
CALLOUT_DESC_CHARS_PER_LINE = 22  # rough wrap width for a 4cm box at \normalsize
CALLOUT_LABEL_MARGIN = 0.35   # minimum breathing room between adjacent labels

def _callout_height(c):
    """Estimate the rendered vertical extent (cm) of one callout's label, so
    labels only reserve as much gutter space as their own text needs instead
    of a one-size-fits-all pitch -- a short "Name / faction" label (no desc)
    packs much tighter than a three-line description."""
    height = CALLOUT_TITLE_LINE_H
    desc = c.get("desc")
    if desc:
        lines = max(1, math.ceil(len(desc) / CALLOUT_DESC_CHARS_PER_LINE))
        height += lines * CALLOUT_DESC_LINE_H
    return height

def _stack_labels(target_ys, gaps):
    """Return label y-positions, one per (already sorted, descending)
    ``target_ys``, that preserve the order, keep at least ``gaps[i]`` between
    label i and i+1, and are the closest possible (least-squares) match to
    the targets -- i.e. leader lines are as close to horizontal as the
    available space allows, and only spread further apart when they must.

    Solved via isotonic regression (pool-adjacent-violators): shifting each
    target by its cumulative minimum gap turns "non-increasing with a minimum
    gap" into a plain non-increasing fit, which PAV solves by merging
    adjacent runs that violate the ordering into their average until none
    remain.
    """
    cum = [0.0] * len(target_ys)
    for i in range(1, len(target_ys)):
        cum[i] = cum[i - 1] + gaps[i - 1]
    shifted = [t + c for t, c in zip(target_ys, cum)]
    blocks = []  # each is [sum, count] of a run of equal (averaged) values
    for value in shifted:
        blocks.append([value, 1])
        while len(blocks) >= 2 and blocks[-2][0] / blocks[-2][1] < blocks[-1][0] / blocks[-1][1]:
            s2, c2 = blocks.pop()
            s1, c1 = blocks.pop()
            blocks.append([s1 + s2, c1 + c2])
    fitted = []
    for s, c in blocks:
        fitted.extend([s / c] * c)
    return [f - c for f, c in zip(fitted, cum)]

def _render_callouts(callouts, present, y_overrides=None):
    """Draw labelled leader lines for every callout whose target node exists.

    ``callouts`` is a list of dicts (side, title, desc, aim); ``side`` is
    "left", "right", or "auto" for elements (e.g. the name plate, spanning the
    full card width) that can be labelled from either gutter -- auto callouts
    are assigned to whichever side has less going on, so a side that's busy
    on one card isn't forced to carry a flexible label too. ``y`` is the
    target's actual height for elements whose position is fixed by the
    layout. ``present`` is the set of node names actually emitted for this
    card, so callouts for optional elements (persistence, faction logo, ...)
    are skipped when absent. ``y_overrides`` supplies the real height for
    elements whose position varies per card (e.g. a weapon's attack/block
    zone), keyed by aim node name, taking precedence over the static ``y``.

    Labels are ordered and stacked (see ``_stack_labels``) by each target's
    real height, so leader lines never cross and stay as close to horizontal
    as each label's own text height allows."""
    # Keep only callouts whose target exists, preserving the listed order, and
    # work on copies since "auto" ones get their side resolved below.
    shown = [dict(c) for c in callouts
             if _aim_node(c["aim"]) is None or _aim_node(c["aim"]) in present]
    y_overrides = y_overrides or {}

    def target_y(c):
        return y_overrides.get(_aim_node(c["aim"]), c["y"])

    # Resolve "auto" sides against the load already fixed on each side (a full
    # first pass, not just whatever precedes it in the list -- an "auto" item
    # near the top must still see the fixed items listed further down), then
    # greedily assign each auto callout to whichever side is currently lighter
    # and add its own height, so several flexible callouts on one card spread
    # across both sides rather than all piling onto whichever side is favoured
    # first.
    load = {"left": 0.0, "right": 0.0}
    for c in shown:
        if c["side"] != "auto":
            load[c["side"]] += _callout_height(c)
    for c in shown:
        if c["side"] == "auto":
            c["side"] = min(load, key=load.get)
            load[c["side"]] += _callout_height(c)

    ys = {}
    for side in ("left", "right"):
        col = sorted((c for c in shown if c["side"] == side), key=target_y, reverse=True)
        gaps = [(_callout_height(a) + _callout_height(b)) / 2 + CALLOUT_LABEL_MARGIN
                for a, b in zip(col, col[1:])]
        for c, y in zip(col, _stack_labels([target_y(c) for c in col], gaps)):
            ys[id(c)] = y

    out = ""
    for i, c in enumerate(shown):
        x = LEFT_GUTTER if c["side"] == "left" else RIGHT_GUTTER
        anchor = "east" if c["side"] == "left" else "west"
        align = "right" if c["side"] == "left" else "left"
        name = f"callout{i}"
        body = "{\\large\\bfseries " + c["title"] + "}"
        if c.get("desc"):
            body += "\\\\" + c["desc"]
        out += (f"\\node[anchor={anchor}, align={align}, text width=4cm, font=\\normalsize] "
                f"({name}) at ({x},{ys[id(c)]}) {{{body}}};\n")
        # Aim at the target's edge nearest the card edge (the gutter side) so the
        # leader stops at the element instead of crossing over it. Named targets
        # (nodes and coordinates) take the .east/.west anchor; bare coordinates
        # are already positioned as points.
        aim = c["aim"]
        if _aim_node(aim) is not None and "." not in aim:
            edge = "east" if c["side"] == "right" else "west"
            aim = f"({_aim_node(aim)}.{edge})"
        out += f"\\draw[thick, gray!60] ({name}.{anchor}) -- {aim};\n"
    return out

# Gutters the callout labels sit in. Pushed a little further out than the card
# edge so the larger label text has breathing room from the artwork.
LEFT_GUTTER = -0.7
RIGHT_GUTTER = 8.6

# Real heights of the fixed (non-zone) rules-box elements, so each callout's
# "y" is where its target actually sits rather than a guess -- _render_callouts
# sorts/stacks labels by this, which is what keeps leader lines from crossing.
# Mirrors the rules_h branch in make_card_from_row (weapon/drone vs pilot).
RULES_H_ACTION = 2.3   # weapon/drone rules box height (cm)
RULES_H_PILOT = 3.0    # pilot rules box height (cm)
ACTION_RULES_TOP_Y = RULES_BOTTOM + RULES_H_ACTION / card_scale
ACTION_RULES_CENTER_Y = (RULES_BOTTOM + ACTION_RULES_TOP_Y) / 2
PILOT_RULES_TOP_Y = RULES_BOTTOM + RULES_H_PILOT / card_scale
PILOT_RULES_CENTER_Y = (RULES_BOTTOM + PILOT_RULES_TOP_Y) / 2
SETINFO_Y = RULES_BOTTOM - 0.02

# Callouts for a weapon action card. atk_aim/block_aim/superblock_aim/range_aim
# are coordinates emitted at build time on the first zone that has that
# feature (see the annotate block in make_card_from_row), at whichever of
# ZONE_CY's three heights that turns out to be -- make_card_from_row passes
# their real per-card height in as a y_override so labels sort correctly even
# though the same callout can point at a different zone on different cards;
# the "y" here is just an unused fallback. The guard in _render_callouts drops
# any callout the chosen card lacks.
WEAPON_CALLOUTS = [
    {"y": NAME_CY, "side": "left",  "title": "Initiative",
     "desc": "Higher acts first.", "aim": "(initbox)"},
    {"y": ACTION_RULES_TOP_Y - 0.42, "side": "left",  "title": "Persistence",
     "desc": "Turns it stays in play.", "aim": "(persistence)"},
    {"y": ACTION_RULES_CENTER_Y, "side": "auto",  "title": "Card text",
     "desc": "Abilities and status effects.", "aim": "(textbox)"},
    {"y": RULES_BOTTOM + 0.13, "side": "left",  "title": "Group zones",
     "desc": "Zones the group can attack (red) or block (blue).", "aim": "(groupindicator)"},
    {"y": NAME_CY, "side": "auto", "title": "Name / faction",
     "desc": "", "aim": "(nameplate)"},
    {"y": NAME_CY, "side": "right", "title": "Movement",
     "desc": "Steps: green gains, red loses.", "aim": "(movebox)"},
    {"y": ZONE_CY["Mid"], "side": "right", "title": "Attack",
     "desc": "Damage dealt to this zone.", "aim": "(atk_aim)"},
    {"y": ZONE_CY["Mid"], "side": "right", "title": "Block",
     "desc": "Blocks attacks to this zone.", "aim": "(block_aim)"},
    {"y": ZONE_CY["Mid"], "side": "right", "title": "Super block",
     "desc": "Blocks without discarding.", "aim": "(superblock_aim)"},
    {"y": ZONE_CY["Mid"], "side": "right", "title": "Range",
     "desc": "This zone's attack range.", "aim": "(range_aim)"},
    {"y": SETINFO_Y, "side": "right", "title": "Set info",
     "desc": "Faction, type, group, flavour.", "aim": "(setinfo)"},
]

# Callouts for a pilot card (no attack, but the three zone boxes still show).
PILOT_CALLOUTS = [
    {"y": NAME_CY, "side": "left",  "title": "Initiative",
     "desc": "Higher acts first.", "aim": "(initbox)"},
    {"y": PILOT_RULES_CENTER_Y, "side": "auto",  "title": "Card text",
     "desc": "This card's effect.", "aim": "(textbox)"},
    {"y": NAME_CY, "side": "auto", "title": "Name / faction",
     "desc": "", "aim": "(nameplate)"},
    {"y": NAME_CY, "side": "right", "title": "Movement",
     "desc": "Steps: green gains, red loses.", "aim": "(movebox)"},
    {"y": PILOT_RULES_TOP_Y - 0.42, "side": "right", "title": "Persistence",
     "desc": "Turns it stays in play.", "aim": "(persistence)"},
    {"y": SETINFO_Y, "side": "right", "title": "Set info",
     "desc": "Faction, type", "aim": "(setinfo)"},
]

# Callouts for a frame datasheet. Armour rows are drawn right-to-left from x=7,
# so the aim points at the rightmost (first) bar of each row. frame_logo/
# frame_ability/loadout heights mirror the literal positions create_frame_sheet
# draws them at (fac_y, the ability box's centre, and stat_ys[1]).
FRAME_CALLOUTS = [
    {"y": NAME_CY, "side": "left",  "title": "Faction Logo",
     "desc": "", "aim": "(frame_logo)"},
    {"y": (ABIL_BOTTOM_Y + ABIL_TOP_Y) / 2, "side": "left",  "title": "Abilities",
     "desc": "", "aim": "(frame_ability)"},
    {"y": 0.5, "side": "left",  "title": "Flavour",
     "desc": "", "aim": "(setinfo)"},
    {"y": NAME_CY, "side": "auto", "title": "Name / faction",
     "desc": "", "aim": "(frame_name)"},
    {"y": NAME_CY, "side": "right", "title": "Movement",
     "desc": "Base movement.", "aim": "(frame_move)"},
    {"y": ZONE_CY["High"], "side": "right", "title": "Top armour",
     "desc": "Top-zone health.", "aim": "(armor_high)"},
    {"y": ZONE_CY["Mid"], "side": "right", "title": "Side armour",
     "desc": "Mid-zone health.", "aim": "(armor_mid)"},
    {"y": ZONE_CY["Low"], "side": "right", "title": "Low armour",
     "desc": "Low-zone health.", "aim": "(armor_low)"},
    {"y": (ABIL_BOTTOM_Y + ABIL_TOP_Y) / 2, "side": "right", "title": "Loadout",
     "desc": "Weapon / booster slots and deck size.", "aim": "(loadout)"},
]

# Callouts for a drone card: a weapon-style card that also fields a persistent
# unit with its own health bar (drone_health) and movement (drone_move), both
# drawn at y=3.9 (see the drone extras block in make_card_from_row). Health is
# listed before movement even though it reads second: they target the same
# height and the health bars sit further from the gutter than the chevron, so
# putting health's tied-height label on the outside (see _stack_labels) keeps
# its leader line from grazing straight through the movement chevron.
DRONE_CALLOUTS = [
    {"y": NAME_CY, "side": "left",  "title": "Initiative",
     "desc": "Higher acts first.", "aim": "(initbox)"},
    {"y": 3.9, "side": "left",  "title": "Drone health",
     "desc": "Drone hit points.", "aim": "(drone_health)"},
    {"y": 3.9, "side": "left",  "title": "Drone movement",
     "desc": "Drone move per turn.", "aim": "(drone_move)"},
    {"y": ACTION_RULES_TOP_Y - 0.42, "side": "left",  "title": "Persistence",
     "desc": "Turns it persists for.", "aim": "(persistence)"},
    {"y": ACTION_RULES_CENTER_Y, "side": "auto",  "title": "Card text",
     "desc": "Abilities and effects.", "aim": "(textbox)"},
    {"y": NAME_CY, "side": "auto", "title": "Name / faction",
     "desc": "", "aim": "(nameplate)"},
    {"y": NAME_CY, "side": "right", "title": "Movement",
     "desc": "Steps when played.", "aim": "(movebox)"},
    {"y": ZONE_CY["Mid"], "side": "right", "title": "Attack",
     "desc": "Damage dealt to this zone.", "aim": "(atk_aim)"},
    {"y": ZONE_CY["Mid"], "side": "right", "title": "Block",
     "desc": "Blocks attacks to this zone.", "aim": "(block_aim)"},
    {"y": ZONE_CY["Mid"], "side": "right", "title": "Super block",
     "desc": "Doubled outline: blocks without discarding.", "aim": "(superblock_aim)"},
    {"y": ZONE_CY["Mid"], "side": "right", "title": "Range",
     "desc": "Drone's attack range.", "aim": "(range_aim)"},
    {"y": SETINFO_Y, "side": "right", "title": "Set info",
     "desc": "Faction, type, group, flavour.", "aim": "(setinfo)"},
]


def make_card_from_row(row, card_type, group_capability=None, annotate=False, annotate_outfile=None):
    outname = (annotate_outfile or 'build/rules_card.tex') if annotate else cardoutputfolder + row['Group'] + "_" + row['Name'] + '.tex'
    is_pilot = card_type is CardTypeEnum.PILOT
    with open(outname, 'w') as ofile:
        # --- card base (flat background; art no longer covers the card) -------
        card_text = f"\\begin{{tikzpicture}}[x={card_scale}cm, y={card_scale}cm]\n "
        card_text += f"\\node (cardbg)[rectangle, minimum width = 6.2cm, minimum height = 8.5cm, fill={CARD_BG}] at (4,5){{}};\n"
        # Pin the picture's bounding box to the card and clip to it, so nothing can
        # spill past the edge and change the card size on the sheet. Skipped when
        # annotating, where the callouts deliberately extend into the gutters.
        if not annotate:
            card_text += "\\useasboundingbox (cardbg.south west) rectangle (cardbg.north east);\n"
            card_text += "\\clip (cardbg.south west) rectangle (cardbg.north east);\n"

        # --- full-card art: spans from just below the name plate down to the
        # bottom of the rules text and the full width of the card. The zone
        # boxes and rules box are drawn over it (at reduced opacity) so the art
        # shows through behind them.
        art_top_y = NAME_CY - (NAME_H_CM / 2) / card_scale
        art_bot_y = RULES_BOTTOM
        art_cy_full = (art_top_y + art_bot_y) / 2
        art_h_full = (art_top_y - art_bot_y) * card_scale  # region height in cm
        card_left = 4 - (6.2 / card_scale) / 2
        card_right = 4 + (6.2 / card_scale) / 2
        card_text += "\\begin{scope}\n"
        card_text += (f"\\clip ({card_left:.3f}, {art_bot_y:.3f}) rectangle "
                      f"({card_right:.3f}, {art_top_y:.3f});\n")
        # background layer fills the whole card width (cover; cropped by the scope)
        if row.get("BackgroundLayer"):
            card_text += (f'\\node at (4,{art_cy_full:.3f}){{\\includegraphics[width=6.2cm,'
                          ' keepaspectratio]{' + images_folder + row["BackgroundLayer"] + '}};\n')
        # the art itself is height-fitted to the region so it is never cut off
        card_text += (f'\\node at (4,{art_cy_full:.3f}){{\\includegraphics[width=6.2cm,'
                      f' max height={art_h_full:.2f}cm, keepaspectratio]{{' + images_folder + row["CardImg"] + '}};\n')
        if row.get("ForegroundImg"):
            card_text += (f'\\node at (4,{art_cy_full:.3f}){{\\includegraphics[width=6.2cm,'
                          f' max height={art_h_full:.2f}cm, keepaspectratio]{{' + images_folder + row["ForegroundImg"] + '}};\n')
        card_text += "\\end{scope}\n"

        # --- name plate with overlapping initiative circle + movement chevron -
        # name plate spans from the init-circle centre to the chevron centre
        name_cx = (INIT_POS[0] + MOVE_POS[0]) / 2
        name_w_cm = (MOVE_POS[0] - INIT_POS[0]) * card_scale
        # name box first so the init circle / chevron overlap its ends on top
        card_text += (f"\\node (nameplate) [rectangle, minimum width={name_w_cm:.2f}cm, minimum height={NAME_H_CM}cm, "
                      f"rounded corners={rc()}, fill=white, draw=black!40, text width={name_w_cm - 1.6:.2f}cm, align=center] "
                      f"at ({name_cx:.2f}, {NAME_CY}){{\\large{{" + row["Name"])
        if row["Faction"]:
            card_text += "}\\\\\n\\small{\\emph{" + row["Faction"] + "}"
        card_text += "}};\n"

        # initiative: a filled circle the height of the name plate, in the top-left
        # corner, overlapping the plate's left end; a symbol sits over the circle
        # behind the number
        card_text += (f"\\node[circle, fill={initiative_color(row['Initiative'])}, draw=black!40, "
                      f"minimum size={NAME_H_CM}cm] (initbox) at ({INIT_POS[0]}, {INIT_POS[1]}){{}};\n")
        card_text += (f"\\node[opacity=0.7] at ({INIT_POS[0]}, {INIT_POS[1]}){{\\includegraphics[width=0.92cm]{{"
                      + icons_folder + initImg + "}};\n")
        card_text += (f"\\node at ({INIT_POS[0]}, {INIT_POS[1]}){{\\Large{{\\textbf{{\\contour{{white}}{{"
                      + row['Initiative'] + "}}}};\n")
        # movement: a chevron the height of the name plate, in the top-right corner,
        # overlapping the plate's right end
        card_text += draw_chevron(MOVE_POS[0], MOVE_POS[1], movement_color(row['Movement']),
                                  row['Movement'], w=MOVE_CHEVRON_W, h=CHEVRON_HALF_H, point=0.5,
                                  fontsize="\\LARGE", name="movebox")

        # --- zone boxes (always three; boundary changes on block/super block) -
        low_cy = PILOT_LOW_CY if is_pilot else ZONE_CY["Low"]
        low_half_h = (PILOT_LOW_H_CM / 2) / card_scale if is_pilot else ZONE_HALF_H
        try:
            if is_pilot:
                card_text += attack_box(0, 0, 1, ZONE_CY["High"], "", "yellow")
                card_text += attack_box(0, 0, 0, ZONE_CY["Mid"], "", "red")
                card_text += attack_box(0, 0, 0, low_cy, "", "blue", half_h=low_half_h)
            else:
                card_text += attack_box(int(row["HighAttack"]), int(row["HighRange"]), int(row["HighBlock"]), ZONE_CY["High"], row["HighDType"], "yellow")
                card_text += attack_box(int(row["MidAttack"]), int(row["MidRange"]), int(row["MidBlock"]), ZONE_CY["Mid"], row["MidDType"], "red")
                card_text += attack_box(int(row["LowAttack"]), int(row["LowRange"]), int(row["LowBlock"]), ZONE_CY["Low"], row["LowDType"], "blue")
        except Exception:
            print(f"exception for {row['Group']} {row['Name']}")
            return ""

        # --- full-width ability / rules box across the bottom -----------------
        # Anchored by its bottom edge (fixed) and grown upward. A pilot's box
        # rises into the space freed by its half-height Low zone; a weapon/drone
        # box stops just below the full-height Low zone so it never overlaps it.
        rules_opacity = 0.7
        if is_pilot:
            rules_h = RULES_H_PILOT
            text_font = "\\small"
        else:
            rules_h = RULES_H_ACTION
            # weapons/drones share the bottom band with the zone column above, so
            # the box height is capped; a smaller font keeps busy cards inside it
            text_font = "\\scriptsize" if estimated_text_len(row["Text"]) > 100 else "\\footnotesize"
        
        if row["Text"]:
            box_fill_draw = f"fill=black!10!white, opacity={rules_opacity}, draw=black!40"
        else:
            # no rules text (e.g. Full Guard): the box stays only for alignment /
            # the faction watermark, so draw neither fill nor border
            box_fill_draw = "fill=none, draw=none"

        card_text += (f"\\node (rulesbox)[anchor=south, rectangle, {box_fill_draw}, rounded corners={rc()}, "
                      f"minimum width={RULES_W_CM}cm, minimum height={rules_h}cm] at (4.0, {RULES_BOTTOM}){{}};\n")

        # faction logo watermark sits above the box fill but behind the text
        if row["Faction"]:
            card_text += ("\\node[opacity=0.6] (factionlogo) at ($(rulesbox.center)+(0.5,0)$) "
                          "{\\includegraphics[width=2.4cm]{" + images_folder + light_logos_dict[row["Faction"]] + "}};\n")

        # rules text, offset right to clear the persistence / group-indicator
        # column; a narrow right gutter lets it use most of the remaining width
        if row["Text"]:
            card_text += (f"\\node[anchor=west, align=left, text width=4.9cm, inner sep=1pt] (textbox) "
                          f"at ($(rulesbox.west)+(0.98,0)$){{{text_font}{{" + row['Text'] + "}};\n")

        # persistence: top-left of the rules box
        if row["Persistence"] != "0":
            card_text += ("\\node (persistence)[circle, fill=red, draw=black!30, minimum size=0.72cm] "
                          "at ($(rulesbox.north west)+(0.42,-0.42)$){\\small{\\textbf{$" + row["Persistence"] + "$}}};\n")

        # drone extras: movement chevron just above the persistence mark, with the
        # health pips in a row to its right (rules box is the non-pilot one here)
        if card_type is CardTypeEnum.DRONE:
            # chevron shifted right so its leftmost edge lines up with the art's
            # left edge (ART left edge in tikz units); health bars sit to its right
            dch_w, dch_h = 0.55, 0.4
            art_left = ART_CX - (ART_W_CM / 2) / card_scale
            dch_cx = art_left + dch_w  # so the chevron's left edge sits on the art's left edge
            card_text += draw_chevron(dch_cx, 3.9, "yellow!85!orange", row['Drone_MV'], h=dch_h, w=dch_w)
            # drone-movement callout is on the left, so aim at the chevron's left edge
            card_text += f"\\coordinate (drone_move) at ({dch_cx - dch_w:.3f}, 3.9);\n"
            card_text += draw_health_bars(int(row["Drone_Health"]), dch_cx + dch_w + 0.28, 3.9, dch_h, name="drone_health")

        # group zone-capability indicator: bottom-left of the rules box
        if group_capability is not None:
            card_text += ("\\node[anchor=south west, inner sep=1pt] (groupindicator) "
                          "at ($(rulesbox.south west)+(0.15,0.13)$){\\input{"
                          + "../build/group_indicator_" + row['Group'] + ".tex}};\n")

        # --- set info + copyright: a tight band chained under the rules box ----
        set_info_content = "{\\scriptsize " + row["Faction"] + "\\hfill " + getTypeName(card_type) + "\\hfill " + row['Group'] + "}"
        if row["Flavor"]:
            set_info_content += "\\\\{\\tiny\\emph{" + row["Flavor"] + "}}"
        card_text += (f"\\node[anchor=north, text width={RULES_W_CM}cm, align=left, inner sep=1pt] (setinfo) "
                      f"at ($(rulesbox.south)+(0,-0.02)$){{" + set_info_content + "};\n")

        if row.get("Artist"):
            card_text += ("\\node[anchor=north, inner sep=1pt] at ($(setinfo.south)+(0,-0.01)$)"
                          "{\\tiny{\\copyright  LiliCo 2026 \\emph{ Art: " + row["Artist"] + "}}};\n")

        if annotate:
            present = {"initbox", "movebox", "nameplate", "setinfo"}
            y_overrides = {}
            if row["Faction"]:
                present.add("factionlogo")
            if row["Persistence"] != "0":
                present.add("persistence")
            if is_pilot:
                if row["Text"]:
                    present.add("textbox")
                callouts = PILOT_CALLOUTS
            else:
                if row["Text"]:
                    present.add("textbox")
                if group_capability is not None:
                    present.add("groupindicator")
                # Representative anchors for the attack/block/range labels, keyed
                # off the new zone-box centres.
                zone_pos = dict(ZONE_CY)
                def first_zone(feat, order):
                    for z in order:
                        if parse_int_safe(row.get(f"{z}{feat}")):
                            return z
                    return None
                def first_block_zone(pred, order=("Mid", "High", "Low")):
                    for z in order:
                        v = parse_int_safe(row.get(f"{z}Block"))
                        if v is not None and pred(v):
                            return z
                    return None
                atk_z = first_zone("Attack", ["High", "Mid", "Low"])
                # normal block (value 1) and super block (value > 1) get separate
                # anchors so each can carry its own callout on the rules cards
                blk_z = first_block_zone(lambda v: v == 1)
                sblk_z = first_block_zone(lambda v: v > 1)
                rng_z = first_zone("Range", ["Low", "Mid", "High"])
                # all zone callouts sit in the right gutter, so aim at the zone's
                # right edge (nearest the card edge) rather than crossing into it
                zone_right = ZONE_CX + ZONE_HALF_W
                if atk_z:
                    card_text += f"\\coordinate (atk_aim) at ({zone_right:.2f},{zone_pos[atk_z]});\n"
                    present.add("atk_aim")
                    y_overrides["atk_aim"] = zone_pos[atk_z]
                if blk_z:
                    card_text += f"\\coordinate (block_aim) at ({zone_right:.2f},{zone_pos[blk_z]});\n"
                    present.add("block_aim")
                    y_overrides["block_aim"] = zone_pos[blk_z]
                if sblk_z:
                    card_text += f"\\coordinate (superblock_aim) at ({zone_right:.2f},{zone_pos[sblk_z]});\n"
                    present.add("superblock_aim")
                    y_overrides["superblock_aim"] = zone_pos[sblk_z]
                if rng_z:
                    card_text += f"\\coordinate (range_aim) at ({zone_right:.2f},{zone_pos[rng_z]});\n"
                    present.add("range_aim")
                    y_overrides["range_aim"] = zone_pos[rng_z]
                if card_type is CardTypeEnum.DRONE:
                    present.add("drone_health")
                    present.add("drone_move")
                    callouts = DRONE_CALLOUTS
                else:
                    callouts = WEAPON_CALLOUTS
            card_text = card_text + _render_callouts(callouts, present, y_overrides)

        card_text = card_text + "\\end{tikzpicture}\n"
        ofile.write(card_text)
        return card_text + "~"
    
def create_frame_sheet(frame, annotate=False, annotate_outfile=None):
    """Frame datasheet, sharing the redesigned card layout: boxed art, name plate
    with a faction-logo box (top-left, where an attack has its initiative) and a
    yellow movement chevron (top-right); the three armour zones line up with an
    attack card's zones (red bars, four fit / a fifth spills); the weapon /
    booster / deck loadout is a column on the right under the zones; the ability
    text sits in a box at the bottom-left with the flavour in the set-info line."""
    outname = (annotate_outfile or 'build/rules_frame.tex') if annotate else frameoutputfolder + frame["Name"] + '.tex'
    with open(outname, 'w') as ofile:
        # --- card base + boxed art (identical to the action cards) ------------
        frame_text = f"\\begin{{tikzpicture}}[x={card_scale}cm, y={card_scale}cm]\n "
        frame_text += f"\\node (cardbg)[rectangle, minimum width = 6.2cm, minimum height = 8.5cm, fill={CARD_BG}] at (4,5){{}};\n"
        # pin bounding box + clip to the card (see make_card_from_row); off when annotating
        if not annotate:
            frame_text += "\\useasboundingbox (cardbg.south west) rectangle (cardbg.north east);\n"
            frame_text += "\\clip (cardbg.south west) rectangle (cardbg.north east);\n"
        # full-card art: spans from just below the name plate down to the bottom
        # of the ability box, full width (as on the action cards). The armour
        # zones, loadout boxes and ability box are drawn over it at reduced
        # opacity so the art shows through behind them.
        art_top_y = NAME_CY - (NAME_H_CM / 2) / card_scale
        art_bot_y = ABIL_BOTTOM_Y  # bottom edge of the ability box
        art_cy_full = (art_top_y + art_bot_y) / 2
        art_h_full = (art_top_y - art_bot_y) * card_scale  # region height in cm
        card_left = 4 - (6.2 / card_scale) / 2
        card_right = 4 + (6.2 / card_scale) / 2
        frame_text += "\\begin{scope}\n"
        frame_text += (f"\\clip ({card_left:.3f}, {art_bot_y:.3f}) rectangle "
                       f"({card_right:.3f}, {art_top_y:.3f});\n")
        # background layer fills the whole card width (cover; cropped by the scope)
        if frame.get("BackgroundLayer"):
            frame_text += (f'\\node at (4,{art_cy_full:.3f}){{\\includegraphics[width=6.2cm,'
                           ' keepaspectratio]{' + frame_images_folder + frame["BackgroundLayer"] + '}};\n')
        # the art itself is height-fitted to the region so it is never cut off
        frame_text += (f'\\node at (4,{art_cy_full:.3f}){{\\includegraphics[width=6.2cm,'
                       f' max height={art_h_full:.2f}cm, keepaspectratio]{{' + frame_images_folder + frame["CardImg"] + '}};\n')
        if frame.get("ForegroundImg"):
            frame_text += (f'\\node at (4,{art_cy_full:.3f}){{\\includegraphics[width=6.2cm,'
                           f' max height={art_h_full:.2f}cm, keepaspectratio]{{' + frame_images_folder + frame["ForegroundImg"] + '}};\n')
        frame_text += "\\end{scope}\n"

        # --- name plate + faction-logo box + movement chevron -----------------
        name_cx = (INIT_POS[0] + MOVE_POS[0]) / 2
        name_w_cm = (MOVE_POS[0] - INIT_POS[0]) * card_scale
        frame_text += (f"\\node (frame_name) [rectangle, minimum width={name_w_cm:.2f}cm, minimum height={NAME_H_CM}cm, "
                       f"rounded corners={rc()}, fill=white, draw=black!40, text width={name_w_cm - 1.6:.2f}cm, align=center] "
                       f"at ({name_cx:.2f}, {NAME_CY}){{\\large{{" + frame["Name"])
        if frame["Faction"]:
            frame_text += "}\\\\\n\\small{\\emph{" + frame["Faction"] + "}"
        frame_text += "}};\n"

        # faction logo box in the top-left corner, standing in for the initiative
        # circle. Sized and positioned exactly like the name plate (same height,
        # centred on NAME_CY) so it aligns top-and-bottom with the name bar; the
        # logo image is forced to fill that height.
        fac_size = NAME_H_CM
        fac_x, fac_y = INIT_POS[0], NAME_CY
        frame_text += (f"\\node[rectangle, rounded corners={rc()}, fill=white, draw=black!40, "
                       f"minimum size={fac_size}cm] (frame_logo) at ({fac_x}, {fac_y}){{}};\n")
        if frame["Faction"]:
            frame_text += (f"\\node at ({fac_x}, {fac_y}){{\\includegraphics[height={fac_size - 0.2:.2f}cm, "
                           f"max width={fac_size - 0.14:.2f}cm, keepaspectratio]{{"
                           + images_folder + logos_dict[frame["Faction"]] + "}};\n")

        # movement chevron (always yellow) in the top-right corner
        frame_text += draw_chevron(MOVE_POS[0], MOVE_POS[1], "yellow", frame['Movement'],
                                   w=MOVE_CHEVRON_W, h=CHEVRON_HALF_H, point=0.5,
                                   fontsize="\\LARGE", name="frame_move")

        # --- armour zones, aligned to the attack-card zones -------------------
        frame_text += draw_armor_bars(int(frame["Top armour"]), FRAME_ZONE_CX, ZONE_CY["High"], penalty="-1Init", name="armor_high")
        frame_text += draw_armor_bars(int(frame["Side armour"]), FRAME_ZONE_CX, ZONE_CY["Mid"], penalty="-1Crd", name="armor_mid")
        frame_text += draw_armor_bars(int(frame["Low armour"]), FRAME_ZONE_CX, ZONE_CY["Low"], penalty="-1Mv", name="armor_low")

        # --- loadout column (weapon / booster / deck) beside the ability box --
        # The three boxes fill the same vertical span as the ability box and share
        # its fill colour / opacity, so the column reads as part of the same panel.
        stat_rows = [(weaponImg, frame["Weapon Slots"]), (boosterImg, frame["Boosters"]),
                     (deckImg, frame["Deck size"])]
        n_stats = len(stat_rows)
        stat_gap = 0.12  # tikz-unit gap between the loadout boxes
        stat_span = ABIL_TOP_Y - ABIL_BOTTOM_Y
        stat_box_h_tikz = (stat_span - (n_stats - 1) * stat_gap) / n_stats
        stat_box_h_cm = stat_box_h_tikz * card_scale
        # centres top→bottom so the rows stay weapon / booster / deck
        stat_ys = [ABIL_BOTTOM_Y + stat_box_h_tikz / 2 + i * (stat_box_h_tikz + stat_gap)
                   for i in reversed(range(n_stats))]
        cell_left = FRAME_ZONE_CX - ZONE_HALF_W
        cell_right = FRAME_ZONE_CX + ZONE_HALF_W
        for (img, val), sy in zip(stat_rows, stat_ys):
            frame_text += (f"\\node[rectangle, draw=black!40, fill=black!10!white, opacity=0.7, rounded corners={rc()}, "
                           f"minimum width={ZONE_W_CM}cm, minimum height={stat_box_h_cm:.2f}cm] at ({FRAME_ZONE_CX}, {sy:.3f}){{}};\n")
            frame_text += (f"\\node[anchor=west] at ({cell_left + 0.12:.3f}, {sy:.3f}){{\\includegraphics[width=0.5cm]{{"
                           + icons_folder + img + "}};\n")
            frame_text += f"\\node[anchor=east] at ({cell_right - 0.16:.3f}, {sy:.3f}){{\\large{{\\textbf{{{val}}}}}}};\n"
        frame_text += f"\\coordinate (loadout) at ({cell_right:.3f}, {(ABIL_BOTTOM_Y + ABIL_TOP_Y) / 2:.3f});\n"

        # --- ability box (bottom-left, beside the loadout column) -------------
        abil_cx, abil_w_cm, abil_h = ABIL_CX, ABIL_W_CM, ABIL_H_CM
        frame_text += (f"\\node (frame_ability)[anchor=south, rectangle, fill=black!10!white, opacity=0.7, draw=black!40, "
                       f"rounded corners={rc()}, minimum width={abil_w_cm}cm, minimum height={abil_h}cm] "
                       f"at ({abil_cx}, {ABIL_BOTTOM_Y}){{}};\n")
        # faction logo watermark behind the ability text (as on the action cards)
        if frame["Faction"]:
            frame_text += ("\\node[opacity=0.6] (factionlogo) at (frame_ability.center) "
                           "{\\includegraphics[width=1.55cm, height=1.7cm, keepaspectratio]{" + images_folder + light_logos_dict[frame["Faction"]] + "}};\n")
        # the ability box is small, so drop to a smaller font for busy frames
        # (e.g. an ability with a \full... macro that expands to a long sentence)
        # so the text doesn't clip out the top/bottom of the box.
        abil_font = "\\scriptsize" if estimated_text_len(frame['Abilities']) > 80 else "\\footnotesize"
        frame_text += (f"\\node[text width={abil_w_cm - 0.4:.1f}cm, align=left, inner sep=1pt, font={abil_font}] "
                       f"at (frame_ability.center){{" + frame['Abilities'] + "};\n")

        # --- flavour + copyright at the bottom (no faction/type line needed:
        # faction is shown up top and the card is obviously a frame). The
        # copyright is pinned just above the card edge and the flavour sits above
        # it with tight line spacing, so a two-line flavour can't push it off.
        if frame.get("Artist"):
            frame_text += ("\\node[anchor=south, inner sep=1pt] at (4.0, 0.12)"
                           "{\\tiny{\\copyright  LiliCo 2026 \\emph{ Art: " + frame["Artist"] + "}}};\n")
        set_info_content = ("{\\tiny\\linespread{0.85}\\selectfont\\emph{" + frame["Flavor"] + "}\\par}"
                            if frame["Flavor"] else "")
        frame_text += (f"\\node[anchor=south, text width={RULES_W_CM}cm, align=center, inner sep=1pt] (setinfo) "
                       f"at (4.0, 0.42){{" + set_info_content + "};\n")

        if annotate:
            present = {"frame_name", "frame_move", "frame_ability", "setinfo",
                       "armor_high", "armor_mid", "armor_low", "loadout"}
            if frame["Faction"]:
                present.add("frame_logo")
            frame_text = frame_text + _render_callouts(FRAME_CALLOUTS, present)

        #finish the tikzpicture
        frame_text = frame_text + "\\end{tikzpicture}\n"

        ofile.write(frame_text)
        return frame_text + "~"


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

def _weapon_is_ranged(row):
    """True if any zone of this weapon has a positive range."""
    return any(parse_int_safe(row.get(f"{z}Range")) for z in zones_order)


def _annotation_score(row, zones=False):
    """Rank a card by how many *distinct* labels it would exercise, so the
    chosen example shows the widest variety of callouts (e.g. a melee weapon
    with attack + block + text beats one that only stacks blocks). Raw damage
    counts are a small tie-breaker."""
    distinct = 0
    raw = 0
    if row.get("Persistence", "0") != "0":
        distinct += 1
    if row.get("Text"):
        distinct += 1
    if row.get("Faction"):
        distinct += 1
    if zones:
        for feat in ("Attack", "Block", "Range"):
            vals = [parse_int_safe(row.get(f"{z}{feat}")) for z in zones_order]
            if any(vals):
                distinct += 1
            raw += sum(v for v in vals if v)
    return distinct * 100 + raw


def _pick(rows, key):
    """max() that tolerates an empty candidate list."""
    return max(rows, key=key) if rows else None


def _block_values(row):
    return [parse_int_safe(row.get(f"{z}Block")) or 0 for z in zones_order]

def _pick_superblock(rows):
    """Pick a weapon card that carries a super block (a zone with block > 1) to
    stand as the labelled Super block example, preferring one that also has a
    normal block so the rules card contrasts both markers side by side."""
    cands = [r for r in rows if any(v > 1 for v in _block_values(r))]
    if not cands:
        return None
    return max(cands, key=lambda r: _annotation_score(r, zones=True)
               + (150 if any(v == 1 for v in _block_values(r)) else 0))


def create_rules_fragments(weapon_rows, weapon_caps, pilot_rows, drone_rows, frame_row):
    """Writes one \\input-able annotated fragment per card type:
        build/rules_weapon_melee.tex, rules_weapon_ranged.tex,
        rules_drone.tex, rules_pilot.tex, rules_frame.tex
    plus build/rules.tex, a standalone preview document that \\inputs them all
    (compile from build/ with: pdflatex rules.tex)."""
    melee_rows  = [r for r in weapon_rows if not _weapon_is_ranged(r)]
    ranged_rows = [r for r in weapon_rows if _weapon_is_ranged(r)]

    fragments = []  # (label, filename) in preview order
    melee = _pick(melee_rows or weapon_rows, lambda r: _annotation_score(r, zones=True))
    if melee is not None:
        make_card_from_row(melee, CardTypeEnum.WEAPON, weapon_caps.get(melee["Group"]),
                           annotate=True, annotate_outfile="build/rules_weapon_melee.tex")
        fragments.append(("Melee weapon", "rules_weapon_melee.tex"))

    ranged = _pick(ranged_rows, lambda r: _annotation_score(r, zones=True))
    if ranged is not None:
        make_card_from_row(ranged, CardTypeEnum.WEAPON, weapon_caps.get(ranged["Group"]),
                           annotate=True, annotate_outfile="build/rules_weapon_ranged.tex")
        fragments.append(("Ranged weapon", "rules_weapon_ranged.tex"))

    superblock = _pick_superblock(melee_rows or weapon_rows)
    if superblock is not None:
        make_card_from_row(superblock, CardTypeEnum.WEAPON, weapon_caps.get(superblock["Group"]),
                           annotate=True, annotate_outfile="build/rules_weapon_superblock.tex")
        fragments.append(("Super block", "rules_weapon_superblock.tex"))

    drone = _pick(drone_rows, lambda r: _annotation_score(r, zones=True))
    if drone is not None:
        make_card_from_row(drone, CardTypeEnum.DRONE, None,
                           annotate=True, annotate_outfile="build/rules_drone.tex")
        fragments.append(("Drone", "rules_drone.tex"))

    pilot = _pick(pilot_rows, _annotation_score)
    if pilot is not None:
        make_card_from_row(pilot, CardTypeEnum.PILOT, None,
                           annotate=True, annotate_outfile="build/rules_pilot.tex")
        fragments.append(("Pilot", "rules_pilot.tex"))

    if frame_row is not None:
        create_frame_sheet(frame_row, annotate=True, annotate_outfile="build/rules_frame.tex")
        fragments.append(("Frame", "rules_frame.tex"))

    with open("build/rules.tex", "w") as ofile:
        ofile.write(header_text)
        ofile.write("\\input{card_macros.tex}\n")
        ofile.write(begin_doc)
        for label, fname in fragments:
            ofile.write("\\begin{center}\n{\\Large\\textbf{" + label + "}}\\par\\vspace{0.6cm}\n")
            ofile.write("\\input{" + fname + "}\n")
            ofile.write("\\end{center}\n\\newpage\n\\noindent\n")
        ofile.write("\\end{document}\n")

    # One standalone single-card document per fragment (build/<stem>_doc.tex) so
    # generate_all_decks can pdflatex+convert each into its own PNG.
    for label, fname in fragments:
        stem = fname[:-len(".tex")]
        with open(f"build/{stem}_doc.tex", "w") as ofile:
            ofile.write(header_text)
            ofile.write("\\input{card_macros.tex}\n")
            ofile.write("\\pagestyle{empty}\n")  # no page number, so -trim crops tight
            ofile.write(begin_doc)
            ofile.write("\\input{" + fname + "}\n")
            ofile.write("\\end{document}\n")


#the actual run
if __name__ == "__main__":
    with open(buildfolder + "card_all.tex", "w") as allfile:
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

        drone_rows = []
        with open(drone_actions_file, "r") as facsvfile:
            reader = csv.DictReader(facsvfile)
            for row in reader:
                if int(row["PrintID"]) > 0:
                        drone_rows.append(row)
                        allfile.write(make_card_from_row(row, CardTypeEnum.DRONE))

        pilot_rows = []
        with open(pilot_actions_file, "r") as facsvfile:
            reader = csv.DictReader(facsvfile)
            for row in reader:
                if int(row["PrintID"]) > 0:
                        pilot_rows.append(row)
                        allfile.write(make_card_from_row(row, CardTypeEnum.PILOT))

        with open(general_action_file, "r") as gencsvfile:
            reader = csv.DictReader(gencsvfile)
            for row in reader:
                if int(row["PrintID"]) > 0:
                    allfile.write(make_card_from_row(row, CardTypeEnum.BASIC))
        rules_frame_row = None
        with open(frames_file, "r") as fcsvfile:
            reader = csv.DictReader(fcsvfile)
            allfile.write("\\newpage \n\\noindent\n")
            for row in reader:
                if int(row["PrintID"]) > 0:
                    if rules_frame_row is None:
                        rules_frame_row = row
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

    # Rules reference: one annotated \input-able fragment per card type
    # (melee weapon, ranged weapon, drone, pilot, frame).
    create_rules_fragments(weapon_rows, weapon_group_caps, pilot_rows, drone_rows, rules_frame_row)
