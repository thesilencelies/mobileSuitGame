#Reads the given CSV of card designs and turns them into TEX files that could be imported as needed

import csv
import enum

weapon_actions_file = 'Weapon actions.csv'
general_action_file = 'Basic actions.csv'
pilot_actions_file = 'Pilot actions.csv'
booster_actions_file = 'Booster actions.csv'
frames_file = 'Frames.csv'
cardoutputfolder='build/card_'
frameoutputfolde='build/frame_'

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

images_folder = "../pictures/"
icons_folder = "../icons/"


frameBackgrounds = ["Ouwa_frame_1.jpeg","Aegis_frame_1.jpeg", "Guild_frame_1.png",
                    "Collective_frame_1.jpeg", "CotN_frame_1.jpeg", "Revolution_frame_1.jpeg"]

iconwidth = "width=0.9cm"
inline_iconwidth = "width=0.5cm"

header_text = "\\documentclass[a4paper, landscape]{article}\n \\usepackage[left =2cm, right = 2cm, " \
            + "top = 1.4cm, bottom =1.4cm]{geometry} \n \\usepackage{tikz} \n \\usepackage[export]{adjustbox}" \
            + "\n \\usetikzlibrary{positioning} \n"

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
    with open(cardoutputfolder + str(i) + '.tex', 'w') as ofile:
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
                "fill=white, opacity=0.75] at (4, 9.2){\\large{" + row["Name"]
        if row["Faction"]:
            card_text = card_text +  "}\n\\emph{" + row["Faction"]
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
            card_text = card_text + "\\node[rectangle, fill = white, opacity = 0.75, minimum height =1.5cm, rounded corners = 0.3cm, " \
                    + "text width = 5.4cm]  at (4, 3.5){\\small{" + row['Text'] +"}g(c};\n"
        else:
            if row["Text"]:
                card_text = card_text + "\\node[rectangle, fill = white, opacity = 0.75, minimum height =1.5cm, rounded corners = 0.3cm, " \
                    + "text width = 3.5cm]  at (2.75, 3.5){\\small{" + row['Text'] +"}};\n"
        
        #set info
        card_text = card_text + "\\node[rectangle, fill = white, opacity = 0.75, minimum width=6cm, minimum height =0.8cm, " \
                + "rounded corners = 0.3cm, text width = 5.8cm]  at (4, 0.7){" \
                + row["Faction"] + " " + getTypeName(card_type) +  " \hfill " + row['Group'] + "\\\\" + \
                "\\emph{" + row["Flavor"] + "}};\n"


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
    with open(frameoutputfolde + str(i) + '.tex', 'w') as ofile:
        """creates the frames datasheet procedurally from the given data"""
        #load the initial image
        frame_text = "\\begin{tikzpicture}[scale=0.86, backbox/.style= {rectangle, minimum height = 2.2cm," \
                + " minimum width =2.2cm, rounded corners = 0.3cm, fill=white, opacity=0.75}]\n "
        frame_text = frame_text + "\\node [rectangle, minimum width = 6.2cm, minimum height = 8.5cm, fill=black!70!white!30] at (4,5){};\n"
        frame_text = frame_text + '\\node at (4,5){\\includegraphics[width=6cm, max height = 8.3cm, keepaspectratio]{' + images_folder + frame["BackgroundImg"] + '}};\n'
        # name
        frame_text = frame_text + "\\node [rectangle, minimum width=4.3cm, minimum height = 1cm,rounded corners = 0.1cm, fill=white, opacity=0.75, text width=4.1cm]" +\
                            "at (3.3, 9){\\large{" + frame["Name"] + "}\\\\\n\\emph{~" + frame["Faction"] + "}};\n"
        
        # movement
        frame_text = frame_text + '\\node at (7,9){\\includegraphics[' + iconwidth + ']{' + icons_folder + mvImg + '}};\n'
        frame_text = frame_text + " \\node at (7,9){\\Large{\\textbf{" + frame['Movement'] +"}}};\n"
        
        # armor
        frame_text = frame_text + draw_armor(int(frame["Top armour"]), 8.5, "-1Init")
        frame_text = frame_text + draw_armor(int(frame["Side armour"]), 7, "-1Crd")
        frame_text = frame_text + draw_armor(int(frame["Low armour"]), 5.5, "-1Mv")
        
        # ability
        frame_text = frame_text + "\\node[rectangle, fill = white, opacity = 0.75, minimum height =1.5cm, rounded corners = 0.3cm, " \
                    + "text width = 5cm]  at (4, 3.5){\\small{" + frame['Abilities'] +"}};\n"

        # weapons
        frame_text = frame_text + "\\node [rectangle, rounded corners = 0.3cm, minimum width=5.5cm, minimum height = 1.8cm, fill = white," + \
                " opacity = 0.75] at (4, 1.2)(bottom_box){};\n"
        frame_text = frame_text + "\\node[anchor=north west, text width = 5.2cm] at (bottom_box.north west){" \
                '\\includegraphics[' + inline_iconwidth + ']{' + icons_folder + weaponImg + '} \\large{ : ' + str(frame["Weapon Slots"]) +  \
                '} ~\\includegraphics[' + inline_iconwidth + ']{' + icons_folder + boosterImg + '}\\large{  : ' + str(frame["Boosters"]) + \
                 "} \\\\\\emph{\\small{" + frame["Flavor"] +  "}}};\n"


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
                    for img in frameBackgrounds:
                        row["BackgroundImg"] = img
                        allfile.write(make_card_from_row(row, i, CardTypeEnum.BASIC))
        j = 0
        with open(frames_file, "r") as fcsvfile:
            reader = csv.DictReader(fcsvfile)
            allfile.write("\\newpage \n")
            for row in reader:
                j = j + 1
                if int(row["Changed"]) > 0:
                    allfile.write(create_frame_sheet(row, j))

        allfile.write("\\end{document}\n")
