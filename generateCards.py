#Reads the given CSV of card designs and turns them into TEX files that could be imported as needed

import csv

weapon_actions_file = 'Gundam Cards - Weapon actions.csv'
general_action_file = 'Gundam Cards - Default actions.csv'
frame_actions_file = 'Gundam Cards - Frame actions.csv'
frames_file = 'Gundam Cards - Frames.csv'
outputfolder='../objects/card_'


mAtkImg = 'attackImg.png'
rAtkImg = 'rattackImg.png'
blkImg = 'blockImg.png'
rangeImg = 'rangeImg.png'
initImg = 'initImg.png'
mvImg = 'mvimg.png'


frameBackgrounds = ["pictures/Hekija_1.jpg","pictures/ReginGlaze_1.jpg", "pictures/Barbatos_1.jpg",
                    "pictures/Bael_1.jpg", "pictures/Flauros_1.jpg", "pictures/Kimaris_1.jpg", "pictures/Julia_1.jpg"]

iconwidth ="width=0.9cm"


def attack_box(atk, rng, block, pos):
    out_text = ""
    # the attack box at the requested location
    if atk or block:
        out_text = out_text + "\\node[backbox] at (6.5, " + str(pos) +"){};\n"
    # what graphic to use
    aimg = rAtkImg if rng > 0 else mAtkImg
    for d in range(0, atk):
        out_text = out_text + "\\node at (" + str(
            -(d / 2) + 7) + ', ' + str(pos + 0.5) + '){\\includegraphics[' + iconwidth + ']{' + aimg + '}};\n'

    # blocks
    for d in range(0, block):
        out_text = out_text + "\\node at (" + str(
            -(d / 2) + 7) + ', ' + str(pos - 0.5) + '{\\includegraphics[' + iconwidth + ']{' + blkImg + '}};\n'
    # ranges
    if rng > 0:
        out_text = out_text + '\\node at ( 6 , ' + str(pos - 0.75) + '){\\includegraphics[' + iconwidth + ']{' + rangeImg + '}};\n'
        out_text = out_text + '\\node at (6, ' + str(pos + 0.75) + '){' + str(rng) + '};\n'

    return out_text


def make_card_from_row(row, i):
    with open(outputfolder + str(i) + '.tex', 'w') as ofile:
        # print("starting card " + str(i))
        # print(row)

        card_text = "\\begin{tikzpicture}[scale=0.86, backbox/.style= {rectangle, minimum height = 2.2cm," \
                   + " minimum width =2.2cm, rounded corners = 0.3cm, fill=white, opacity=0.65}]\n "
        card_text = card_text + "\\node [rectangle, minimum width = 6.2cm, minimum height = 8.5cm, fill=black!70!white!30] at (4,5){};\n"
        card_text = card_text + '\\node at (4,5){\\includegraphics[width=6cm]{' + row["BackgroundImg"] + '}};\n'
        # format the card
        card_text = card_text + "\\node [rectangle, minimum height = 1.2cm,rounded corners = 0.3cm, fill=white, opacity=0.6] at (4, 9.5){\\large{" + row["Name"] + "}};\n"
        # default symbols
        card_text = card_text + '\\node at(1.5,9){\\includegraphics[' + iconwidth + ']{' + initImg + '}};\n'
        card_text = card_text +" \\node at (1.5, 9){\\large{" + row['Initiative'] +"}};\n"
        card_text = card_text + '\\node at (1.5,8){\\includegraphics[' + iconwidth + ']{' + mvImg + '}};\n'
        card_text = card_text + " \\node at (1.5,8){\\large{" + row['Movement'] +"}};\n"

        if bool(row["OneUse"]):
             card_text = card_text + "\\node at (4.5,9)[circle, draw, fill = red]{\\LARGE{\\textbf{O}}};\n"

        card_text = card_text + attack_box(int(row["HighAttack"]), int(row["HighRange"]), int(row["HighBlock"]), 7.5)
        card_text = card_text + attack_box(int(row["MidAttack"]), int(row["MidRange"]), int(row["MidBlock"]), 4.5)
        card_text = card_text + attack_box(int(row["LowAttack"]), int(row["LowRange"]), int(row["LowBlock"]), 1.5)

        #set info
        card_text = card_text + "\\node[rectangle, fill = white, minimum height =1.5cm, " \
                + "rounded corners = 0.3cm, text width = 1.6cm, opacity = 0.65]  at (2, 7){ \\small{" \
                +  row['Slot type'] + " \\\\ " + row['Slot name'] + " : " + str(row['Slot number']) + "}};\n"

        # textbox
        if row["Text"]:
            card_text = card_text + "\\node[rectangle, fill = white, minimum height =1.5cm, rounded corners = 0.3cm, " \
                    + "text width = 3.5cm, opacity = 0.65]  at (3, 1.5){" + row['Text'] +"};\n"

        card_text = card_text + "\\end{tikzpicture}\n"
        ofile.write(card_text)
        return card_text


def create_frame_sheet(frame):
    """creates the frames datasheet procedurally from the given data"""
    #load the initial image
    frame_text = "\\begin{tikzpicture}[scale=1, backbox/.style= {rectangle, minimum height = 2.4cm," \
               + " minimum width =2.8cm, rounded corners = 0.3cm, fill=white, opacity=0.65}]\n "
    frame_text = frame_text + "\\node [rectangle, minimum width = 25.2cm, minimum height = 18.5cm, fill=black!70!white!30] at (0, 0){};\n"
    frame_text = frame_text + '\\node at (0,0){\\includegraphics[height=18.5cm]{' + frame["BackgroundImg"] + '}};\n'

    #generate the body as a graph
    frame_text = frame_text + "\\node (chest) at (0,0) [backbox] {chest \\\\ death};\n"
    frame_text = frame_text + "\\node (pelvis) [backbox, above = of chest] {chest \\\\ death};\n"
    #TODO finish this


    #mark the three damage zones
    frame_text = frame_text + "\\node [rectangle, minimum width = 25.2cm, minimum height = 6cm, fill=red, opacity = 0.4] at (0, 6){};\n"
    frame_text = frame_text + "\\node [rectangle, minimum width = 25.2cm, minimum height = 6cm, fill=green, opacity = 0.4] at (0, 0){};\n"
    frame_text = frame_text + "\\node [rectangle, minimum width = 25.2cm, minimum height = 6cm, fill=blue, opacity = 0.4] at (0, -6){};\n"


    #finish the tikzpicture
    frame_text = frame_text + "\\end{tikzpicture}\n"

    return frame_text


#the actual run

if __name__ == "__main__":

    with open(outputfolder + "all.tex", "w") as allfile:
        allfile.write("\\documentclass[a4paper, landscape]{article}\n \\usepackage[left =2cm, right = 2cm, " \
                        + "top = 1.4cm, bottom =1.4cm]{geometry} \n \\usepackage{tikz} \n \\begin{document}\n")

        with open(weapon_actions_file, "r") as spcsvfile:
            reader = csv.DictReader(spcsvfile)
            i = 0
            # special moves
            for row in reader:
                i = i + 1
                if row["Changed"]:
                    # construct a card for every one in the range
                    for counter in range(0, int(row["Slot number"])):
                        allfile.write(make_card_from_row(row, i))

        with open(frame_actions_file, "r") as facsvfile:
            reader = csv.DictReader(facsvfile)
            i = 0
            # special moves
            for row in reader:
                i = i + 1
                if row["Changed"]:
                    # construct a card for every one in the range
                    for counter in range(0, int(row["Slot number"])):
                        allfile.write(make_card_from_row(row, i))

        with open(general_action_file, "r") as gencsvfile:
            reader = csv.DictReader(gencsvfile)
            i = 0
            # generic moves
            for row in reader:
                i = i + 1
                if row["Changed"]:
                    for img in frameBackgrounds:
                        row["BackgroundImg"] = img
                        # construct a card for every one in the range
                        for counter in range(0, int(row["Slot number"])):
                            allfile.write(make_card_from_row(row, i))

        with open(general_action_file, "r") as fcsvfile:
            reader = csv.DictReader(fcsvfile)
            for row in reader:
                allfile.write("\\newpage \n")
                allfile.write(create_frame_sheet(row))

        allfile.write("\\end{document}\n")