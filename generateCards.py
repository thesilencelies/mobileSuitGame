#Reads the given CSV of card designs and turns them into TEX files that could be imported as needed

import csv

weapon_actions_file = 'Gundam Cards - Weapon actions.csv'
general_action_file = 'Gundam Cards - Default actions.csv'
frame_actions_file = 'Gundam Cards - Frame actions.csv'
frames_file = 'Gundam Cards - Frames.csv'
outputfolder='build/card_'


mAtkImg = 'Sword.png'
rAtkImg = 'Bullet.png'
blkImg = 'Block.png'
rangeImg = 'rangeimg.png'
initImg = 'initiative.png'
mvImg = 'mvimg.png'

images_folder = "pictures/"
icons_folder = "images/"


frameBackgrounds = ["Hekija_1.jpg","Reginglaze_1.jpg", "Barbatos_1.jpg",
                    "Bael_1.jpg", "Flauros_1.jpg", "Kimaris_vidar_2.jpg", "Julia_1.jpg"]

iconwidth ="width=0.9cm"


def attack_box(atk, rng, block, pos):
    out_text = ""
    # the attack box at the requested location
    if atk or block:
        out_text = out_text + "\\node[backbox] at (6.5, " + str(pos) +"){};\n"
    # what graphic to use
    aimg = icons_folder + (rAtkImg if rng > 0 else mAtkImg)
    for d in range(0, atk):
        out_text = out_text + "\\node at (" + str(
            -(d / 2) + 7.0) + ', ' + str(pos + 0.5) + '){\\includegraphics[' + iconwidth + ']{' + aimg + '}};\n'

    # blocks
    for d in range(0, block):
        out_text = out_text + "\\node at (" + str(
            -(d / 2) + 7.0) + ', ' + str(pos - 0.5) + '){\\includegraphics[' + iconwidth + ']{' + icons_folder + \
                   blkImg + '}};\n'
    # ranges
    if rng > 0:
        out_text = out_text + '\\node at ( 6 , ' + str(pos - 0.55) + '){\\includegraphics[' + iconwidth + ']{' + \
                   icons_folder + rangeImg + '}};\n'
        out_text = out_text + '\\node at (6, ' + str(pos + 0.1) + '){\\Large{' + str(rng) + '}};\n'

    return out_text


def make_card_from_row(row, i):
    with open(outputfolder + str(i) + '.tex', 'w') as ofile:
        # print("starting card " + str(i))
        # print(row)

        card_text = "\\begin{tikzpicture}[scale=0.86, backbox/.style= {rectangle, minimum height = 2.2cm," \
                   + " minimum width =2.2cm, rounded corners = 0.3cm, fill=white, opacity=0.75}]\n "
        card_text = card_text + "\\node [rectangle, minimum width = 6.2cm, minimum height = 8.5cm, fill=black] at (4,5){};\n"
        card_text = card_text + '\\node at (4,5){\\includegraphics[width=6cm]{' + images_folder + row["BackgroundImg"] + '}};\n'
        # format the card
        card_text = card_text + "\\node [rectangle, minimum height = 1.2cm,rounded corners = 0.3cm, fill=white, opacity=0.6] at (4, 9.5){\\large{" + row["Name"] + "}};\n"
        # default symbols
        card_text = card_text + '\\node at(1.5,9){\\includegraphics[' + iconwidth + ']{' + icons_folder + initImg + '}};\n'
        card_text = card_text +" \\node at (1.5, 9){\\Large{\\textbf{" + row['Initiative'] +"}}};\n"
        card_text = card_text + '\\node at (1.5,8){\\includegraphics[' + iconwidth + ']{' + icons_folder + mvImg + '}};\n'
        card_text = card_text + " \\node at (1.5,8){\\Large{\\textbf{" + row['Movement'] +"}}};\n"

        if int(row["OneUse"]) > 0:
             card_text = card_text + "\\node at (7,9.5)[circle, fill = red]{\\large{\\textbf{O}}};\n"

        card_text = card_text + attack_box(int(row["HighAttack"]), int(row["HighRange"]), int(row["HighBlock"]), 7.5)
        card_text = card_text + attack_box(int(row["MidAttack"]), int(row["MidRange"]), int(row["MidBlock"]), 4.5)
        card_text = card_text + attack_box(int(row["LowAttack"]), int(row["LowRange"]), int(row["LowBlock"]), 1.5)

        #set info
        card_text = card_text + "\\node[rectangle, fill = white, opacity = 0.75, minimum height =1.3cm, " \
                + "rounded corners = 0.3cm, text width = 2.1cm]  at (2, 3.5){ \\footnotesize{" \
                +  row['Slot type'] + " \\\\ " + row['Slot name'] + " : " + str(row['Slot number']) + "}};\n"

        # textbox
        if row["Text"]:
            card_text = card_text + "\\node[rectangle, fill = white, opacity = 0.75, minimum height =1.5cm, rounded corners = 0.3cm, " \
                    + "text width = 3.5cm]  at (3, 1.5){\\small{" + row['Text'] +"}};\n"

        card_text = card_text + "\\end{tikzpicture}\n"
        ofile.write(card_text)
        return card_text + "~"


def create_frame_sheet(frame):
    """creates the frames datasheet procedurally from the given data"""
    #load the initial image
    frame_text = "\\begin{tikzpicture}[scale=1, backbox/.style= {rectangle, minimum height = 2.4cm," \
               + " minimum width =2.8cm, rounded corners = 0.3cm, fill=white, opacity=0.75}," \
               + " every text node part/.style={align=center, font = \\Large}, node distance = 2.5cm]\n "
    frame_text = frame_text + "\\node [rectangle, minimum width = 25.2cm, minimum height = 18.5cm, fill=black!70!white!30] at (0, 0){};\n"
    frame_text = frame_text + '\\node at (0,0){\\includegraphics[height=18.5cm]{' + images_folder + frame["BackgroundImg"] + '}};\n'

    #mark the three damage zones
    frame_text = frame_text + "\\node [rectangle, minimum width = 25.2cm, minimum height = 6cm, fill=red, opacity = 0.1] at (0, 6){};\n"
    frame_text = frame_text + "\\node [rectangle, minimum width = 25.2cm, minimum height = 6cm, fill=green, opacity = 0.1] at (0, 0){};\n"
    frame_text = frame_text + "\\node [rectangle, minimum width = 25.2cm, minimum height = 6cm, fill=blue, opacity = 0.1] at (0, -6){};\n"


    #generate the body as a graph
    frame_text = frame_text + "\\node (chest) at (0,1) [backbox] {{chest \\\\ \\textit{death}};\n"
    frame_text = frame_text + "\\node (pelvis) [backbox, below = of chest] {pelvis \\\\ \\textit{-1 action}}\n edge (chest);\n"
    frame_text = frame_text + "\\node (head) [backbox, above = of chest] {head \\\\ \\textit{-3 inititative}}\n edge (chest);\n"
    frame_text = frame_text + "\\node (l arm) [backbox, left = of chest] {arm \\\\ \\textit{-1 card}}\n edge (chest);\n"
    frame_text = frame_text + "\\node (r arm) [backbox, right = of chest] {arm \\\\ \\textit{-1 card}}\n edge (chest);\n"
    frame_text = frame_text + "\\node (l leg) [backbox, below left = of pelvis] {leg \\\\ \\textit{-1 movement}}\n edge (pelvis);\n"
    frame_text = frame_text + "\\node (r leg) [backbox, below right = of pelvis] {leg \\\\ \\textit{-1 movement}}\n edge (pelvis);\n"

    #armour
    if int(frame["Top armour"]) > 0:
        frame_text = frame_text + "\\node (top l armour) [backbox, above left = of chest] {armour}\n edge (chest);\n"
    if int(frame["Top armour"]) > 1:
        frame_text = frame_text + "\\node (top r armour) [backbox, above right = of chest] {armour}\n edge (chest);\n"

    if int(frame["Side armour"]) > 0:
        frame_text = frame_text + "\\node (mid l armour) [backbox, left = of l arm] {armour}\n edge (l arm);\n"
    if int(frame["Side armour"]) > 1:
        frame_text = frame_text + "\\node (mid r armour) [backbox, right = of r arm] {armour}\n edge (r arm);\n"


    if int(frame["Low armour"]) > 0:
        frame_text = frame_text + "\\node (low l armour) [backbox, left = of pelvis] {armour}\n edge (pelvis);\n"
    if int(frame["Low armour"]) > 1:
        frame_text = frame_text + "\\node (low r armour) [backbox, right = of pelvis] {armour}\n edge (pelvis);\n"

    #boosters
    if int(frame["Boosters"]) > 0:
        frame_text = frame_text + "\\node (booster) at (8,4) [backbox] {boosters \\\\ \\textit{-1 mv}}\n edge (chest);\n"

    #finish the tikzpicture
    frame_text = frame_text + "\\end{tikzpicture}\n"

    return frame_text


#the actual run

if __name__ == "__main__":

    with open(outputfolder + "all.tex", "w") as allfile:
        allfile.write("\\documentclass[a4paper, landscape]{article}\n \\usepackage[left =2cm, right = 2cm, " \
                        + "top = 1.4cm, bottom =1.4cm]{geometry} \n \\usepackage{tikz} \n \\usetikzlibrary{positioning} \\begin{document}\n")

        with open(weapon_actions_file, "r") as spcsvfile:
            reader = csv.DictReader(spcsvfile)
            i = 0
            # special moves
            for row in reader:
                i = i + 1
                if int(row["Changed"]) > 0:
                    # construct a card for every one in the range
                    for counter in range(0, int(row["Slot number"])):
                        allfile.write(make_card_from_row(row, i))

        with open(frame_actions_file, "r") as facsvfile:
            reader = csv.DictReader(facsvfile)
            i = 0
            # special moves
            for row in reader:
                i = i + 1
                if int(row["Changed"]) > 0:
                    # construct a card for every one in the range
                    for counter in range(0, int(row["Slot number"])):
                        allfile.write(make_card_from_row(row, i))

        with open(general_action_file, "r") as gencsvfile:
            reader = csv.DictReader(gencsvfile)
            i = 0
            # generic moves
            for row in reader:
                i = i + 1
                if int(row["Changed"]) > 0:
                    for img in frameBackgrounds:
                        row["BackgroundImg"] = img
                        # construct a card for every one in the range
                        for counter in range(0, int(row["Slot number"])):
                            allfile.write(make_card_from_row(row, i))

        with open(frames_file, "r") as fcsvfile:
            reader = csv.DictReader(fcsvfile)
            for row in reader:
                allfile.write("\\newpage \n")
                allfile.write(create_frame_sheet(row))

        allfile.write("\\end{document}\n")
