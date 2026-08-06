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

buildfolder='build/'
# Per-card, frame and terrain tiles live in their own subfolders
# (build/card/<Group>_<Name>.tex, build/frame/<Name>.tex, build/terrain/<Name>.tex)
# rather than using card_/frame_/terrain_ filename prefixes.
cardoutputfolder=buildfolder+'card/'
frameoutputfolder=buildfolder+'frame/'
backsoutputfolder=buildfolder+'back_'
terrianoutputfolder=buildfolder+'terrain/'
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
        out_text += block_shield_outline(cx, cy, color, half_w, half_h, super_block=block > 1)
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

# Vertical layout of the callout labels. Rather than pinning each label to a
# fixed y next to its target, the labels are spread evenly down each gutter with
# a fixed pitch, centred on the card. A side with many labels therefore spills
# above and below the card edges instead of being crammed alongside it, which
# keeps the diagram far less cluttered and leaves room for larger text.
CALLOUT_PITCH = 1.6       # cm between successive labels on one side
CALLOUT_CENTER_Y = 5.0    # card vertical centre the stacks are balanced around

def _render_callouts(callouts, present):
    """Draw labelled leader lines for every callout whose target node exists.

    ``callouts`` is a list of dicts (x, y, side, title, desc, aim). ``present``
    is the set of node names actually emitted for this card, so callouts for
    optional elements (persistence, faction logo, ...) are skipped when absent.
    The per-callout ``y`` is only an ordering hint: labels are re-spaced evenly
    down each gutter (extending past the card top and bottom when a side is
    busy) so the leader lines stay legible."""
    # Keep only callouts whose target exists, preserving the listed order.
    shown = [c for c in callouts
             if _aim_node(c["aim"]) is None or _aim_node(c["aim"]) in present]

    # Assign an evenly spaced y per side, centred on the card so a busy side
    # overflows symmetrically above and below the card rather than bunching up.
    ys = {}
    for side in ("left", "right"):
        col = [c for c in shown if c["side"] == side]
        top = CALLOUT_CENTER_Y + (len(col) - 1) * CALLOUT_PITCH / 2.0
        for j, c in enumerate(col):
            ys[id(c)] = top - j * CALLOUT_PITCH

    out = ""
    for i, c in enumerate(shown):
        anchor = "east" if c["side"] == "left" else "west"
        align = "right" if c["side"] == "left" else "left"
        name = f"callout{i}"
        body = "{\\large\\bfseries " + c["title"] + "}"
        if c.get("desc"):
            body += "\\\\" + c["desc"]
        out += (f"\\node[anchor={anchor}, align={align}, text width=4cm, font=\\normalsize] "
                f"({name}) at ({c['x']},{ys[id(c)]}) {{{body}}};\n")
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

# Callouts for a weapon action card. atk_aim/block_aim/range_aim are coordinates
# emitted at build time on the first zone that has that feature (see the annotate
# block in make_card_from_row); the guard in _render_callouts drops any that the
# chosen card lacks.
WEAPON_CALLOUTS = [
    {"x": LEFT_GUTTER, "y": 9.2, "side": "left",  "title": "Initiative",
     "desc": "Higher acts first.", "aim": "(initbox)"},
    {"x": LEFT_GUTTER, "y": 4.0, "side": "left",  "title": "Persistence",
     "desc": "Turns it stays in play.", "aim": "(persistence)"},
    {"x": LEFT_GUTTER, "y": 3.0, "side": "left",  "title": "Card text",
     "desc": "Abilities and status effects.", "aim": "(textbox)"},
    {"x": LEFT_GUTTER, "y": 2.2, "side": "left",  "title": "Faction Logo",
     "desc": "", "aim": "(factionlogo)"},
    {"x": LEFT_GUTTER, "y": 1.2, "side": "left",  "title": "Group zones",
     "desc": "Zones the group can attack (red) or block (blue).", "aim": "(groupindicator)"},
    {"x": RIGHT_GUTTER, "y": 9.4, "side": "right", "title": "Name / faction",
     "desc": "", "aim": "(nameplate)"},
    {"x": RIGHT_GUTTER, "y": 8.0, "side": "right", "title": "Movement",
     "desc": "Steps: green gains, red loses.", "aim": "(movebox)"},
    {"x": RIGHT_GUTTER, "y": 7.0, "side": "right", "title": "Attack",
     "desc": "Damage dealt to this zone.", "aim": "(atk_aim)"},
    {"x": RIGHT_GUTTER, "y": 5.6, "side": "right", "title": "Block",
     "desc": "Blocks attacks to this zone.", "aim": "(block_aim)"},
    {"x": RIGHT_GUTTER, "y": 4.4, "side": "right", "title": "Super block",
     "desc": "Blocks without discarding; keeps blocking.", "aim": "(superblock_aim)"},
    {"x": RIGHT_GUTTER, "y": 3.2, "side": "right", "title": "Range",
     "desc": "This zone's attack range.", "aim": "(range_aim)"},
    {"x": RIGHT_GUTTER, "y": 1.5, "side": "right", "title": "Set info",
     "desc": "Faction, type, group, flavour.", "aim": "(setinfo)"},
]

# Callouts for a pilot card (no attack, but the three zone boxes still show).
PILOT_CALLOUTS = [
    {"x": LEFT_GUTTER, "y": 9.0, "side": "left",  "title": "Initiative",
     "desc": "Higher acts first.", "aim": "(initbox)"},
    {"x": LEFT_GUTTER, "y": 3.2, "side": "left",  "title": "Card text",
     "desc": "This card's effect.", "aim": "(textbox)"},
    {"x": LEFT_GUTTER, "y": 2.0, "side": "left",  "title": "Faction Logo",
     "desc": "", "aim": "(factionlogo)"},
    {"x": RIGHT_GUTTER, "y": 9.2, "side": "right", "title": "Name / faction",
     "desc": "", "aim": "(nameplate)"},
    {"x": RIGHT_GUTTER, "y": 7.8, "side": "right", "title": "Movement",
     "desc": "Steps: green gains, red loses.", "aim": "(movebox)"},
    {"x": RIGHT_GUTTER, "y": 4.0, "side": "right", "title": "Persistence",
     "desc": "Turns it stays in play.", "aim": "(persistence)"},
    {"x": RIGHT_GUTTER, "y": 1.5, "side": "right", "title": "Set info",
     "desc": "Faction, type, flavour.", "aim": "(setinfo)"},
]

# Callouts for a frame datasheet. Armour rows are drawn right-to-left from x=7,
# so the aim points at the rightmost (first) bar of each row.
FRAME_CALLOUTS = [
    {"x": LEFT_GUTTER, "y": 9.2, "side": "left",  "title": "Faction Logo",
     "desc": "", "aim": "(frame_logo)"},
    {"x": LEFT_GUTTER, "y": 3.0, "side": "left",  "title": "Abilities",
     "desc": "Innate special ability.", "aim": "(frame_ability)"},
    {"x": LEFT_GUTTER, "y": 1.2, "side": "left",  "title": "Flavour",
     "desc": "", "aim": "(setinfo)"},
    {"x": RIGHT_GUTTER, "y": 9.4, "side": "right", "title": "Name / faction",
     "desc": "", "aim": "(frame_name)"},
    {"x": RIGHT_GUTTER, "y": 8.2, "side": "right", "title": "Movement",
     "desc": "Base movement.", "aim": "(frame_move)"},
    {"x": RIGHT_GUTTER, "y": 7.0, "side": "right", "title": "Top armour",
     "desc": "Top-zone health.", "aim": "(armor_high)"},
    {"x": RIGHT_GUTTER, "y": 5.6, "side": "right", "title": "Side armour",
     "desc": "Mid-zone health.", "aim": "(armor_mid)"},
    {"x": RIGHT_GUTTER, "y": 4.2, "side": "right", "title": "Low armour",
     "desc": "Low-zone health.", "aim": "(armor_low)"},
    {"x": RIGHT_GUTTER, "y": 2.4, "side": "right", "title": "Loadout",
     "desc": "Weapon / booster slots and deck size.", "aim": "(loadout)"},
]

# Callouts for a drone card: a weapon-style card that also fields a persistent
# unit with its own health bar (drone_health) and movement (drone_move).
DRONE_CALLOUTS = [
    {"x": LEFT_GUTTER, "y": 9.3, "side": "left",  "title": "Initiative",
     "desc": "Higher acts first.", "aim": "(initbox)"},
    {"x": LEFT_GUTTER, "y": 4.6, "side": "left",  "title": "Drone movement",
     "desc": "Drone move per turn.", "aim": "(drone_move)"},
    {"x": LEFT_GUTTER, "y": 3.8, "side": "left",  "title": "Drone health",
     "desc": "Drone hit points.", "aim": "(drone_health)"},
    {"x": LEFT_GUTTER, "y": 3.0, "side": "left",  "title": "Persistence",
     "desc": "Rounds it persists.", "aim": "(persistence)"},
    {"x": LEFT_GUTTER, "y": 2.2, "side": "left",  "title": "Card text",
     "desc": "Abilities and effects.", "aim": "(textbox)"},
    {"x": LEFT_GUTTER, "y": 1.4, "side": "left",  "title": "Faction Logo",
     "desc": "", "aim": "(factionlogo)"},
    {"x": RIGHT_GUTTER, "y": 9.4, "side": "right", "title": "Name / faction",
     "desc": "", "aim": "(nameplate)"},
    {"x": RIGHT_GUTTER, "y": 8.2, "side": "right", "title": "Movement",
     "desc": "Steps when played.", "aim": "(movebox)"},
    {"x": RIGHT_GUTTER, "y": 6.8, "side": "right", "title": "Attack",
     "desc": "Damage dealt to this zone.", "aim": "(atk_aim)"},
    {"x": RIGHT_GUTTER, "y": 5.2, "side": "right", "title": "Block",
     "desc": "Blocks attacks to this zone.", "aim": "(block_aim)"},
    {"x": RIGHT_GUTTER, "y": 4.1, "side": "right", "title": "Super block",
     "desc": "Doubled outline: blocks without discarding.", "aim": "(superblock_aim)"},
    {"x": RIGHT_GUTTER, "y": 3.0, "side": "right", "title": "Range",
     "desc": "Drone's attack range.", "aim": "(range_aim)"},
    {"x": RIGHT_GUTTER, "y": 1.8, "side": "right", "title": "Set info",
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
            rules_h = 3.0
            text_font = "\\small"
        else:
            rules_h = 2.3
            # weapons/drones share the bottom band with the zone column above, so
            # the box height is capped; a smaller font keeps busy cards inside it
            text_font = "\\scriptsize" if estimated_text_len(row["Text"]) > 100 else "\\footnotesize"
        
        if not row["Text"]:
            # make rules box invisible
            rules_opacity = 0

        card_text += (f"\\node (rulesbox)[anchor=south, rectangle, fill=black!10!white, opacity={rules_opacity}, draw=black!40, rounded corners={rc()}, "
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
                if blk_z:
                    card_text += f"\\coordinate (block_aim) at ({zone_right:.2f},{zone_pos[blk_z]});\n"
                    present.add("block_aim")
                if sblk_z:
                    card_text += f"\\coordinate (superblock_aim) at ({zone_right:.2f},{zone_pos[sblk_z]});\n"
                    present.add("superblock_aim")
                if rng_z:
                    card_text += f"\\coordinate (range_aim) at ({zone_right:.2f},{zone_pos[rng_z]});\n"
                    present.add("range_aim")
                if card_type is CardTypeEnum.DRONE:
                    present.add("drone_health")
                    present.add("drone_move")
                    callouts = DRONE_CALLOUTS
                else:
                    callouts = WEAPON_CALLOUTS
            card_text = card_text + _render_callouts(callouts, present)

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
        frame_text += (f"\\node (artbox)[rectangle, fill=white, draw=black!40, rounded corners={rc()}, "
                       f"minimum width={ART_W_CM}cm, minimum height={ART_H_CM}cm] at ({ART_CX}, {ART_CY}){{}};\n")
        frame_text += "\\begin{scope}\n"
        frame_text += f"\\clip[rounded corners={rc()}] ($(artbox.south west)+(0.03,0.03)$) rectangle ($(artbox.north east)+(-0.03,-0.03)$);\n"
        if frame.get("BackgroundLayer"):
            frame_text += (f'\\node at ({ART_CX},{ART_CY}){{\\includegraphics[width={ART_W_CM}cm,'
                           ' keepaspectratio]{' + frame_images_folder + frame["BackgroundLayer"] + '}};\n')
        frame_text += (f'\\node at ({ART_CX},{ART_CY}){{\\includegraphics[width={ART_W_CM - 0.1:.2f}cm, max height={ART_H_CM - 0.1:.2f}cm,'
                       ' keepaspectratio]{' + frame_images_folder + frame["CardImg"] + '}};\n')
        if frame.get("ForegroundImg"):
            frame_text += (f'\\node at ({ART_CX},{ART_CY}){{\\includegraphics[width={ART_W_CM - 0.1:.2f}cm, max height={ART_H_CM - 0.1:.2f}cm,'
                           ' keepaspectratio]{' + frame_images_folder + frame["ForegroundImg"] + '}};\n')
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

        # faction logo in a box in the top-left corner (a little bigger than the
        # initiative circle it stands in for), fully opaque. Its centre is nudged
        # down/right of INIT_POS and its size kept modest so the (larger) box
        # still sits a few pixels inside the top-left corner of the card.
        fac_size = 1.2
        fac_x, fac_y = 1.2, 9.14
        frame_text += (f"\\node[rectangle, rounded corners={rc()}, fill=white, draw=black!40, "
                       f"minimum size={fac_size}cm] (frame_logo) at ({fac_x}, {fac_y}){{}};\n")
        if frame["Faction"]:
            frame_text += (f"\\node at ({fac_x}, {fac_y}){{\\includegraphics[width=1.0cm]{{"
                           + images_folder + logos_dict[frame["Faction"]] + "}};\n")

        # movement chevron (always yellow) in the top-right corner
        frame_text += draw_chevron(MOVE_POS[0], MOVE_POS[1], "yellow", frame['Movement'],
                                   w=MOVE_CHEVRON_W, h=CHEVRON_HALF_H, point=0.5,
                                   fontsize="\\LARGE", name="frame_move")

        # --- armour zones, aligned to the attack-card zones -------------------
        frame_text += draw_armor_bars(int(frame["Top armour"]), ZONE_CX, ZONE_CY["High"], penalty="-1Init", name="armor_high")
        frame_text += draw_armor_bars(int(frame["Side armour"]), ZONE_CX, ZONE_CY["Mid"], penalty="-1Crd", name="armor_mid")
        frame_text += draw_armor_bars(int(frame["Low armour"]), ZONE_CX, ZONE_CY["Low"], penalty="-1Mv", name="armor_low")

        # --- loadout column (weapon / booster / deck) under the zones ---------
        stat_rows = [(weaponImg, frame["Weapon Slots"]), (boosterImg, frame["Boosters"]),
                     (deckImg, frame["Deck size"])]
        stat_ys = [3.02, 2.30, 1.58]
        cell_left = ZONE_CX - ZONE_HALF_W
        cell_right = ZONE_CX + ZONE_HALF_W
        for (img, val), sy in zip(stat_rows, stat_ys):
            frame_text += (f"\\node[rectangle, draw=black!45, fill=white, rounded corners={rc()}, "
                           f"minimum width={ZONE_W_CM}cm, minimum height=0.62cm] at ({ZONE_CX}, {sy}){{}};\n")
            frame_text += (f"\\node[anchor=west] at ({cell_left + 0.12:.3f}, {sy}){{\\includegraphics[width=0.5cm]{{"
                           + icons_folder + img + "}};\n")
            frame_text += f"\\node[anchor=east] at ({cell_right - 0.16:.3f}, {sy}){{\\large{{\\textbf{{{val}}}}}}};\n"
        frame_text += f"\\coordinate (loadout) at ({cell_right:.3f}, {stat_ys[1]});\n"

        # --- ability box (bottom-left, beside the loadout column) -------------
        abil_cx, abil_w_cm, abil_h = 2.86, 4.1, 2.0
        frame_text += (f"\\node (frame_ability)[anchor=south, rectangle, fill=black!10!white, draw=black!40, "
                       f"rounded corners={rc()}, minimum width={abil_w_cm}cm, minimum height={abil_h}cm] "
                       f"at ({abil_cx}, 1.05){{}};\n")
        # faction logo watermark behind the ability text (as on the action cards)
        if frame["Faction"]:
            frame_text += ("\\node[opacity=0.6] (factionlogo) at (frame_ability.center) "
                           "{\\includegraphics[width=1.55cm, height=1.7cm, keepaspectratio]{" + images_folder + light_logos_dict[frame["Faction"]] + "}};\n")
        frame_text += (f"\\node[text width={abil_w_cm - 0.4:.1f}cm, align=left, inner sep=1pt, font=\\footnotesize] "
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
