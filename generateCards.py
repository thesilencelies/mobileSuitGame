#Reads the given CSV of card designs and turns them into TEX files that could be imported as needed

import csv

weapon_actions_file = 'Gundam Cards - Weapon actions.csv'
general_action_file = 'Gundam Cards - Default actions.csv'
pilot_actions_file = 'Gundam Cards - Pilot actions.csv'
booster_actions_file = 'Gundam Cards - Booster actions.csv'
frames_file = 'Gundam Cards - Frames.csv'
cardoutputfolder='build/card_'
frameoutputfolde='build/frame_'

#icon names
mAtkImg = 'attackImg.png'
rAtkImg = 'rattackImg.png'
blkImg = 'blockImg.png'
rangeImg = 'rangeImg.png'
initImg = 'initImg.png'
mvImg = 'mvimg.png'
weaponImg = 'weapon.png'
boosterImg = 'boosterImg.png'

images_folder = "../pictures/"
icons_folder = "../icons/"


frameBackgrounds = ["Hekija_1.jpg","Reginglaze_1.jpg", "Barbatos_1.jpg",
                    "Bael_1.jpg", "Flauros_1.jpg", "Kimaris_vidar_2.jpg", "Julia_1.jpg"]

iconwidth ="width=0.9cm"

header_text = "\\documentclass[a4paper, landscape]{article}\n \\usepackage[left =2cm, right = 2cm, " \
            + "top = 1.4cm, bottom =1.4cm]{geometry} \n \\usepackage{tikz} \n \\usepackage[export]{adjustbox} \n \\usetikzlibrary{positioning} \n\\begin{document}\n\\noindent\n"

def attack_box(atk, rng, block, pos):
    out_text = ""
    # the attack box at the requested location
    if atk or block:
        out_text = out_text + "\\node[backbox] at (6.2, " + str(pos) +"){};\n"
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
        out_text = out_text + '\\node at ( 5.9, ' + str(pos - 0.55) + '){\\includegraphics[' + iconwidth + ']{' + \
                   icons_folder + rangeImg + '}};\n'
        out_text = out_text + '\\node at (5.9, ' + str(pos - 0.1) + '){\\Large{' + str(rng) + '}};\n'

    return out_text


def make_card_from_row(row, i):
    with open(cardoutputfolder + str(i) + '.tex', 'w') as ofile:
        # print("starting card " + str(i))
        # print(row)

        card_text = "\\begin{tikzpicture}[scale=0.86, backbox/.style= {rectangle, minimum height = 2.0cm," \
                   + " minimum width =2.0cm, rounded corners = 0.3cm, fill=white, opacity=0.75}]\n "
        card_text = card_text + "\\node [rectangle, minimum width = 6.2cm, minimum height = 8.5cm, fill=black] at (4,5){};\n"
        card_text = card_text + '\\node at (4,5){\\includegraphics[width=6cm, max height = 8.3cm, keepaspectratio]{' + images_folder + row["BackgroundImg"] + '}};\n'
        # format the card
        # name
        card_text = card_text + "\\node [rectangle, minimum width=4cm, minimum height = 0.6cm,rounded corners = 0.1cm, fill=white, opacity=0.75] at (4, 9.2){\\large{" + row["Name"] + "}};\n"
        # default symbols
        card_text = card_text + '\\node at(1, 9.2){\\includegraphics[' + iconwidth + ']{' + icons_folder + initImg + '}};\n'
        card_text = card_text +" \\node at (1, 9.2){\\Large{\\textbf{" + row['Initiative'] +"}}};\n"
        card_text = card_text + '\\node at (1.1, 8.2){\\includegraphics[' + iconwidth + ']{' + icons_folder + mvImg + '}};\n'
        card_text = card_text + " \\node at (1, 8.2){\\Large{\\textbf{" + row['Movement'] +"}}};\n"

        if int(row["OneUse"]) > 0:
             card_text = card_text + "\\node at (7,9.2)[circle, fill = red]{\\large{\\textbf{O}}};\n"

        card_text = card_text + attack_box(int(row["HighAttack"]), int(row["HighRange"]), int(row["HighBlock"]), 7.5)
        card_text = card_text + attack_box(int(row["MidAttack"]), int(row["MidRange"]), int(row["MidBlock"]), 5.0)
        card_text = card_text + attack_box(int(row["LowAttack"]), int(row["LowRange"]), int(row["LowBlock"]), 2.5)

        # textbox
        if row["Text"]:
            card_text = card_text + "\\node[rectangle, fill = white, opacity = 0.75, minimum height =1.5cm, rounded corners = 0.3cm, " \
                    + "text width = 3.5cm]  at (2.75, 3.5){\\small{" + row['Text'] +"}};\n"
        #set info
        card_text = card_text + "\\node[rectangle, fill = white, opacity = 0.75, minimum width=6cm, minimum height =0.8cm, " \
                + "rounded corners = 0.3cm, text width = 5.8cm]  at (4, 0.7){" \
                +  row['Slot type'] + " :  " + row['Slot name'] + "};\n"


        card_text = card_text + "\\end{tikzpicture}\n"
        # ofile.write(header_text)
        ofile.write(card_text)
        # ofile.write("\\end{document}\n")
        return card_text + "~"
    
def make_pilot_from_row(row, i):
    with open(cardoutputfolder + str(i) + '.tex', 'w') as ofile:
        # print("starting card " + str(i))
        # print(row)

        card_text = "\\begin{tikzpicture}[scale=0.86, backbox/.style= {rectangle, minimum height = 2.2cm," \
                   + " minimum width =2.2cm, rounded corners = 0.3cm, fill=white, opacity=0.75}]\n "
        card_text = card_text + "\\node [rectangle, minimum width = 6.2cm, minimum height = 8.5cm, fill=black] at (4,5){};\n"
        card_text = card_text + '\\node at (4,5){\\includegraphics[width=6cm, max height = 8.3cm, keepaspectratio]{' + images_folder + row["BackgroundImg"] + '}};\n'
        # format the card
        # name
        card_text = card_text + "\\node [rectangle, minimum width=4cm, minimum height = 0.6cm,rounded corners = 0.1cm, fill=white, opacity=0.6] at (4, 9.2){\\large{" + row["Name"] + "}};\n"
        # default symbols
        card_text = card_text + '\\node at(1, 9.2){\\includegraphics[' + iconwidth + ']{' + icons_folder + initImg + '}};\n'
        card_text = card_text +" \\node at (1, 9.2){\\Large{\\textbf{" + row['Initiative'] +"}}};\n"
        card_text = card_text + '\\node at (1.1, 8.2){\\includegraphics[' + iconwidth + ']{' + icons_folder + mvImg + '}};\n'
        card_text = card_text + " \\node at (1, 8.2){\\Large{\\textbf{" + row['Movement'] +"}}};\n"

        # always high block
        card_text = card_text + attack_box(0, 0, 1, 7.5)

        if int(row["OneUse"]) > 0:
             card_text = card_text + "\\node at (7,9.2)[circle, fill = red]{\\large{\\textbf{O}}};\n"


        # textbox
        card_text = card_text + "\\node[rectangle, fill = white, opacity = 0.75, minimum height =1.5cm, rounded corners = 0.3cm, " \
                    + "text width = 5.4cm]  at (4, 3.5){\\small{" + row['Text'] +"}};\n"
        #set info
        card_text = card_text + "\\node[rectangle, fill = white, opacity = 0.75, minimum width=6cm, minimum height =0.8cm, " \
                + "rounded corners = 0.3cm, text width = 5.8cm]  at (4, 0.7){" \
                +  row['Slot type'] + " : " + row['Slot name'] + "};\n"

        card_text = card_text + "\\end{tikzpicture}\n"
        # ofile.write(header_text)
        ofile.write(card_text)
        # ofile.write("\\end{document}\n")
        return card_text + "~"
    

def create_frame_sheet(frame, i):
    with open(frameoutputfolde + str(i) + '.tex', 'w') as ofile:
        """creates the frames datasheet procedurally from the given data"""
        #load the initial image
        frame_text = "\\begin{tikzpicture}[scale=0.86, backbox/.style= {rectangle, minimum height = 2.2cm," \
                + " minimum width =2.2cm, rounded corners = 0.3cm, fill=white, opacity=0.75}]\n "
        frame_text = frame_text + "\\node [rectangle, minimum width = 6.2cm, minimum height = 8.5cm, fill=black!70!white!30] at (4,5){};\n"
        frame_text = frame_text + '\\node at (4,5){\\includegraphics[width=6cm, max height = 8.3cm, keepaspectratio]{' + images_folder + frame["BackgroundImg"] + '}};\n'
        # name
        frame_text = frame_text + "\\node [rectangle, minimum width=4.3cm, minimum height = 1cm,rounded corners = 0.1cm, fill=white, opacity=0.75] at (3.3, 9){\\large{" + frame["Name"] + "}};\n"
        
        # movement
        frame_text = frame_text + '\\node at (7,9){\\includegraphics[' + iconwidth + ']{' + icons_folder + mvImg + '}};\n'
        frame_text = frame_text + " \\node at (7,9){\\Large{\\textbf{" + frame['Movement'] +"}}};\n"
        
        # armor
        frame_text = frame_text + "\\node [rectangle, minimum width=2cm, minimum height = 1cm, fill = red, opacity = 0.75] at (6.5, 7.5){"+ frame["Top armour"]+"};\n"
        frame_text = frame_text + "\\node [rectangle, minimum width=2cm, minimum height = 1cm, fill = red, opacity = 0.75] at (6.5, 5.5){"+ frame["Side armour"]+"};\n"
        frame_text = frame_text + "\\node [rectangle, minimum width=2cm, minimum height = 1cm, fill = red, opacity = 0.75] at (6.5, 3.5){"+ frame["Low armour"]+"};\n"

        # ability
        frame_text = frame_text + "\\node[rectangle, fill = white, opacity = 0.75, minimum height =1.5cm, rounded corners = 0.3cm, " \
                    + "text width = 3.5cm]  at (3, 3.5){\\small{" + frame['Abilities'] +"}};\n"

        # weapons
        frame_text = frame_text + "\\node [rectangle, rounded corners = 0.3cm, minimum width=5cm, minimum height = 1cm, fill = white,  opacity = 0.75] at (4, 1.2){" + \
                '\\includegraphics[' + iconwidth + ']{' + icons_folder + weaponImg + '} \\large{ : ' + str(frame["Weapon Slots"]) +  \
                '} ~\\includegraphics[' + iconwidth + ']{' + icons_folder + boosterImg + '}\\large{  : ' + str(frame["Boosters"]) + "}};\n"


        #finish the tikzpicture
        frame_text = frame_text + "\\end{tikzpicture}\n"

        ofile.write(frame_text)
        return frame_text + "~"


#the actual run
if __name__ == "__main__":
    with open(cardoutputfolder + "all.tex", "w") as allfile:
        allfile.write(header_text)
        i = 0
        with open(weapon_actions_file, "r") as spcsvfile:
            reader = csv.DictReader(spcsvfile)
            for row in reader:
                i = i + 1
                if int(row["Changed"]) > 0:
                        allfile.write(make_card_from_row(row, i))

        with open(booster_actions_file, "r") as facsvfile:
            reader = csv.DictReader(facsvfile)
            for row in reader:
                i = i + 1
                if int(row["Changed"]) > 0:
                        allfile.write(make_card_from_row(row, i))

        with open(pilot_actions_file, "r") as facsvfile:
            reader = csv.DictReader(facsvfile)
            for row in reader:
                i = i + 1
                if int(row["Changed"]) > 0:
                        allfile.write(make_pilot_from_row(row, i))

        with open(general_action_file, "r") as gencsvfile:
            reader = csv.DictReader(gencsvfile)
            for row in reader:
                i = i + 1
                if int(row["Changed"]) > 0:
                    for img in frameBackgrounds:
                        row["BackgroundImg"] = img
                        allfile.write(make_card_from_row(row, i))
        j = 0
        with open(frames_file, "r") as fcsvfile:
            reader = csv.DictReader(fcsvfile)
            allfile.write("\\newpage \n")
            for row in reader:
                j = j + 1
                if int(row["Changed"]) > 0:
                    allfile.write(create_frame_sheet(row, j))

        allfile.write("\\end{document}\n")
