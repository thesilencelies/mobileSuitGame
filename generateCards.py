#Reads the given CSV of card designs and turns them into TEX files that could be imported as needed

import csv

datafile = 'data.csv'

mAtkImg = 'attackImg.png'
rAtkImg = 'rattackImg.png'
blkImg = 'blockImg.png'
rangeImg = 'rangeImg.png'
initImg = 'initImg.png'
mvImg = 'mvimg.png'

outputfolder='objects/card_'
with open(datafile) as csvfile:
     reader = csv.DictReader(csvfile)
     i = 0
     for row in reader:
         #CONSTRUCT A NEW CARD
         with open(outputfolder + str(i) + '.tex', 'w') as ofile:
            print("starting card " + str(i))
            i = i + 1
            #background image should be loaded here?
            filetext = "\\begin{tikzpicture}[scale=0.5]\n "
            filetext = filetext + '\\node(0,0){\\includegraphics[width=8cm]{' + row["BackgroundImg"] + '}};\n'
            #format the card
            #default symbols
            #lightning motif
            #filetext = filetext + "\\draw[blue](1.1,1.9) -- (1.05,1.95)\n"
            filetext = filetext + '\\node(1,2){\\includegraphics{' + initImg + '}};\n'
            filetext = filetext + "\\node(1,2)[circle, minimum width = 1cm, fill opacity = 0.3, fill = white!30!blue!70, color = white!40!blue!60, draw] {}; \\node(1,2){" + row['Initiative'] +"};\n" 
            #filetext = filetext + "\\node[rectangle, minimum width = 1cm, fill opacity = 0.3, fill = white!30!yellow!70, color = white!40!yellow!60, draw] (1, 2){};"
            filetext = filetext + '\\node(1,3){\\includegraphics{' + mvImg + '}};\n'
            filetext = filetext + " \\node(1,3){" + row['Movement'] +"};\n" 
            if row["Basicness"] == "y":
                 filetext = filetext + "\\node (1,4)[text = green]{\\Large{B}};\n"
                 
            print("starting attacks")
            #attacks
            if int(row["HighAttack"]) or int(row["HighBlock"]):
                filetext = filetext + "\\node[rectangle, minimum height = 1cm, minimum width =2cm, rounded corners=0.3cm, fill=white, opacity=0.3](4, 2){};\n"
            aimg = rAtkImg if int(row["HighRange"]) > 0 else mAtkImg
            for d in range(0,int(row["HighAttack"])):
                filetext = filetext + "\\node(" + str(-i + 7) + ', 1){\\includegraphics{' + aimg + '}};\n'
            
            #blocks
            for d in range(0,int(row["HighBlock"])):
                filetext = filetext + "\\node(" + str(i - 7) + ', 2){\\includegraphics{' + blkImg + '}};\n'
            #ranges
            if int(row["HighRange"]) > 0:
                 filetext = filetext + '\\node( 7 , 2){\\includegraphics{' + rangeImg + '}};\n'
                 filetext = filetext + '\\node(7, 3){' + row["HighRange"] + '};\n'
            print("mid")
              #attacks
            if int(row["MidAttack"]) or int(row["MidBlock"]):
                filetext = filetext + "\\node[rectangle, minimum height = 1cm, minimum width =2cm, rounded corners= 0.3cm, fill=white, opacity=0.3](4, 5){};\n"
            aimg = rAtkImg if int(row["MidRange"]) > 0 else mAtkImg
            for d in range(0,int(row["MidAttack"])):
                filetext = filetext + "\\node(" + str(-i + 7) + ', 5){\\includegraphics{'+ aimg + '}};\n'
            
            #blocks
            for d in range(0,int(row["MidBlock"])):
                filetext = filetext + "\\node(" + str(i - 7) + ', 6){\\includegraphics{' + blkImg + '}};\n'
            #ranges
            if int(row["MidRange"]) > 0:
                 filetext = filetext + '\\node( 7 , 5){\\includegraphics{' + rangeImg + '}};\n'
                 filetext = filetext + '\\node(7, 5){' + row["MidRange"] + '};\n'
            print("low")
              #attacks
            if int(row["LowAttack"]) or int(row["LowBlock"]):
                filetext = filetext + "\\node[rectangle, minimum height = 1cm, minimum width =2cm, rounded corners = 0.3cm, fill=white, opacity=0.3](4, 8){};\n"
            aimg = rAtkImg if int(row["LowRange"]) > 0 else mAtkImg
            for d in range(0,int(row["LowAttack"])):
                filetext = filetext + "\\node(" + str(-i + 7) + ', 7){\\includegraphics{' + aimg + '}};\n'
            
            #blocks
            for d in range(0,int(row["LowBlock"])):
                filetext = filetext + "\\node(" + str(i - 7) + ', 8){\\includegraphics{' + blkImg + '}};\n'
            #ranges
            if int(row["LowRange"]) > 0:
                 filetext = filetext + '\\node( 7 , 7){\\includegraphics{' + rangeImg + '}};\n'
                 filetext = filetext + '\\node(7, 8){' + row["LowRange"] + '};\n'
            print("text")
            #cardtext
            filetext = filetext + "\\node[rectangle, fill opacity = 0.3, fill = white, rounded corners = 0.3cm, draw, text width = 5cm] (1, 20){" + row['Text'] +"};\n" 
            filetext = filetext + "\\end{tikzpicture}\n"
            ofile.write(filetext)