#Reads the given CSV of card designs and turns them into TEX files that could be imported as needed

import csv

weapon_actions_file = 'special_actions.csv'
general_action_file = 'default_actions.csv'
frame_actions_file = ''
frames_file = ''


mAtkImg = 'attackImg.png'
rAtkImg = 'rattackImg.png'
blkImg = 'blockImg.png'
rangeImg = 'rangeImg.png'
initImg = 'initImg.png'
mvImg = 'mvimg.png'

frameImgs = ["HekijaFrame.png", "RegingrazeFrame.png", "BarbatosFrame.png", "BaelFrame.png", "KimarisFrame.png", "FlaurosFrame.png"]

frameBackgrounds = ["Hekija.png","ReginGraze.png", "Barbatos.png", "Bael.png", "Flauros.png", "Kimaris.png"]

iconwidth ="width=0.9cm"


def attack_box(atk, range, block, pos):
    otext = ""
    # the attack box at the requested location
    if atk or block:
        otext = otext + "\\node[backbox] at (6.5, " + str(pos) +"){};\n"
    # what graphic to use
    aimg = rAtkImg if range > 0 else mAtkImg
    for d in range(0, atk):
        otext = otext + "\\node at (" + str(
            -(d / 2) + 7) + ', ' + str(pos + 0.5) + '){\\includegraphics[' + iconwidth + ']{' + aimg + '}};\n'

    # blocks
    for d in range(0, block):
        otext = otext + "\\node at (" + str(
            -(d / 2) + 7) + ', ' + str(pos - 0.5) + '{\\includegraphics[' + iconwidth + ']{' + blkImg + '}};\n'
    # ranges
    if range > 0:
        otext = otext + '\\node at ( 6 , ' + str(pos - 0.75) + '){\\includegraphics[' + iconwidth + ']{' + rangeImg + '}};\n'
        otext = otext + '\\node at (6, ' + str(pos + 0.75) + '){' + str(range) + '};\n'


def make_card_from_row(row, i):
    with open(outputfolder + str(i) + '.tex', 'w') as ofile:
        print("starting card " + str(i))
        print(row)

        cardtext = "\\begin{tikzpicture}[scale=0.86, backbox/.style= {rectangle, minimum height = 2.2cm," \
                   + " minimum width =2.2cm, rounded corners = 0.3cm, fill=white, opacity=0.65}]\n "
        cardtext = cardtext + "\\node [rectangle, minimum width = 6.2cm, minimum height = 8.5cm, fill=black!70!white!30] at (4,5){};\n"
        cardtext = cardtext + '\\node at (4,5){\\includegraphics[width=6cm]{' + row["BackgroundImg"] + '}};\n'
        # format the card
        cardtext = cardtext + "\\node [rectangle, minimum height = 1.2cm,rounded corners = 0.3cm, fill=white, opacity=0.6] at (4, 9.5){\\large{" + row["Name"] + "}};\n"
        # default symbols
        cardtext = cardtext + '\\node at(1.5,9){\\includegraphics[' + iconwidth + ']{' + initImg + '}};\n'
        cardtext = cardtext +" \\node at (1.5, 9){\\large{" + row['Initiative'] +"}};\n"
        cardtext = cardtext + '\\node at (1.5,8){\\includegraphics[' + iconwidth + ']{' + mvImg + '}};\n'
        cardtext = cardtext + " \\node at (1.5,8){\\large{" + row['Movement'] +"}};\n"

        if bool(row["OneUse"]):
             cardtext = cardtext + "\\node at (4.5,9)[circle, draw, fill = red]{\\LARGE{\\textbf{O}}};\n"

        cardtext = cardtext + attack_box(int(row["HighAttack"]), int(row["HighRange"]), int(row["HighBlock"]), 7.5)
        cardtext = cardtext + attack_box(int(row["MidAttack"]), int(row["MidRange"]), int(row["MidBlock"]), 4.5)
        cardtext = cardtext + attack_box(int(row["LowAttack"]), int(row["LowRange"]), int(row["LowBlock"]), 1.5)

        #set info
        cardtext = cardtext + "\\node[rectangle, fill = white, minimum height =1.5cm, " \
                + "rounded corners = 0.3cm, text width = 1.6cm, opacity = 0.65]  at (2, 7){ \\small{" \
                +  row['Slot type'] + " \\\\ " + row['Slot name'] + " : " + str(row['Slot number']) + "}};\n"

        # textbox
        if row["Text"]:
            cardtext = cardtext + "\\node[rectangle, fill = white, minimum height =1.5cm, rounded corners = 0.3cm, " \
                    + "text width = 3.5cm, opacity = 0.65]  at (3, 1.5){" + row['Text'] +"};\n"

        cardtext = cardtext + "\\end{tikzpicture}\n"
        ofile.write(cardtext)
        return cardtext


def create_frame_sheet(frame):
    """creates the frames datasheet procedurally from the given data"""
    #TODO - write this
    return ""


outputfolder='objects/card_'
with open(outputfolder + "all.tex", "w") as allfile:
    allfile.write("\\documentclass[a4paper, landscape]{article}\n \\usepackage[left =2cm, right = 2cm, " \
                    + "top = 1.4cm, bottom =1.4cm]{geometry} \n \\usepackage{tikz} \n \\begin{document}\n")
     
    with open(weapon_actions_file, "r") as spcsvfile:
        reader = csv.DictReader(spcsvfile)
        i = 0
        # special moves
        for row in reader:
            if row["Changed"]:
                # construct a card for every one in the range
                for counter in range(0, row["Slot Number"]):
                    allfile.write(make_card_from_row(row, i))

    with open(frame_actions_file, "r") as facsvfile:
        reader = csv.DictReader(facsvfile)
        i = 0
        # special moves
        for row in reader:
            if row["Changed"]:
                # construct a card for every one in the range
                for counter in range(0, row["Slot Number"]):
                    allfile.write(make_card_from_row(row, i))

    with open(general_action_file, "r") as gencsvfile:
        reader = csv.DictReader(gencsvfile)
        i = 0
        # generic moves
        for row in reader:
            if row["Changed"]:
                for img in frameBackgrounds:
                    row["BackgroundImg"] = img
                    # construct a card for every one in the range
                    for counter in range(0, row["Slot Number"]):
                        allfile.write(make_card_from_row(row, i))

    with open(general_action_file, "r") as fcsvfile:
        reader = csv.DictReader(fcsvfile)
        for row in reader:
            allfile.write("\\newpage \n")
            allfile.write(create_frame_sheet(row))

    # frames
    # deprecated
    for frame in frameImgs:
        filetext = "\\begin{tikzpicture}[scale=0.86, backbox/.style= {rectangle, minimum height = 2.2cm, minimum width =2.2cm, rounded corners = 0.3cm, fill=white, opacity=0.65}]\n "
        filetext = filetext + "\\node [rectangle, minimum width = 6.2cm, minimum height = 8.5cm, fill=black!70!white!30] at (4,5){};\n"
        filetext = filetext + '\\node at (4,5){\\includegraphics[width=6cm]{' + frame + '}};\n'
        filetext = filetext + "\\end{tikzpicture}\n"
        allfile.write(filetext)

     allfile.write("\\end{document}\n")