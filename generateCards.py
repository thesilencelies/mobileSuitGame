#Reads the given CSV of card designs and turns them into TEX files that could be imported as needed

import csv

datafile = 'data.csv'

mAtkImg = 'attackImg.png'
rAtkImg = 'rattackImg.png'
blkImg = 'blockImg.png'
rangeImg = 'rangeImg.png'
initImg = 'initImg.png'
mvImg = 'mvimg.png'

iconwidth ="width=0.8cm"

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
            filetext = "\\begin{tikzpicture}[scale=1, backbox/.style= {rectangle, minimum height = 2.2cm, minimum width =2.2cm, rounded corners = 0.3cm, fill=white, opacity=0.3}]\n "
            filetext = filetext + "\\node [rectangle, minimum width = 8cm, minimum height = 10cm] at (4,5){};\n"
            filetext = filetext + '\\node at (4,5){\\includegraphics[width=8cm]{' + row["BackgroundImg"] + '}};\n'
            #format the card
            filetext = filetext + "\\node [rectangle, minimum height = 1.2cm,rounded corners = 0.3cm, fill=white, opacity=0.6] at (4, 10){" + row["Name"] + "};\n"
            #default symbols
            #lightning motif
            #filetext = filetext + "\\draw[blue](1.1,1.9) -- (1.05,1.95)\n"
            filetext = filetext + '\\node at(1,9){\\includegraphics[' + iconwidth + ']{' + initImg + '}};\n'
            #filetext = filetext + "\\node at (0,9)[circle, minimum width = 1cm, fill opacity = 0.3, fill = white!30!blue!70, color = white!40!blue!60, draw] {};"
            filetext = filetext +" \\node at (01, 9){" + row['Initiative'] +"};\n" 
            #filetext = filetext + "\\node[rectangle, minimum width = 1cm, fill opacity = 0.3, fill = white!30!yellow!70, color = white!40!yellow!60, draw] at (1, 9){};"
            filetext = filetext + '\\node at (01,8){\\includegraphics[' + iconwidth + ']{' + mvImg + '}};\n'
            filetext = filetext + " \\node at (1,8){" + row['Movement'] +"};\n" 
            if row["Basicness"] == "y":
                 filetext = filetext + "\\node at (01,7)[text = green]{\\LARGE{\\textbf{B}}};\n"
                 
            print("starting attacks")
            #attacks
            if int(row["HighAttack"]) or int(row["HighBlock"]):
                filetext = filetext + "\\node[backbox] at (6.5, 9.5){};\n"
            aimg = rAtkImg if int(row["HighRange"]) > 0 else mAtkImg
            for d in range(0,int(row["HighAttack"])):
                filetext = filetext + "\\node at (" + str(-d + 7) + ', 10){\\includegraphics[' + iconwidth + ']{' + aimg + '}};\n'
            
            #blocks
            for d in range(0,int(row["HighBlock"])):
                filetext = filetext + "\\node at (" + str(d + 7) + ', 9){\\includegraphics[' + iconwidth + ']{' + blkImg + '}};\n'
            #ranges
            if int(row["HighRange"]) > 0:
                 filetext = filetext + '\\node at ( 7 , 9){\\includegraphics[' + iconwidth + ']{' + rangeImg + '}};\n'
                 filetext = filetext + '\\node at (7, 8){' + row["HighRange"] + '};\n'
            print("mid")
              #attacks
            if int(row["MidAttack"]) or int(row["MidBlock"]):
                filetext = filetext + "\\node[backbox] at (6.5, 6.5){};\n"
            aimg = rAtkImg if int(row["MidRange"]) > 0 else mAtkImg
            for d in range(0,int(row["MidAttack"])):
                filetext = filetext + "\\node at (" + str(-d + 7) + ', 6){\\includegraphics[' + iconwidth + ']{'+ aimg + '}};\n'
            
            #blocks
            for d in range(0,int(row["MidBlock"])):
                filetext = filetext + "\\node at (" + str(d + 7) + ', 5){\\includegraphics[' + iconwidth + ']{' + blkImg + '}};\n'
            #ranges
            if int(row["MidRange"]) > 0:
                 filetext = filetext + '\\node at ( 7 , 6){\\includegraphics[' + iconwidth + ']{' + rangeImg + '}};\n'
                 filetext = filetext + '\\node at (7, 6){' + row["MidRange"] + '};\n'
            print("low")
            
              #attacks
            if int(row["LowAttack"]) or int(row["LowBlock"]):
                filetext = filetext + "\\node[backbox] at (6.5, 3.5){};\n"
            aimg = rAtkImg if int(row["LowRange"]) > 0 else mAtkImg
            for d in range(0,int(row["LowAttack"])):
                filetext = filetext + "\\node at (" + str(-d + 7) + ', 4){\\includegraphics[' + iconwidth + ']{' + aimg + '}};\n'
            
            #blocks
            for d in range(0,int(row["LowBlock"])):
                filetext = filetext + "\\node at (" + str(d + 7) + ', 3){\\includegraphics[width=1cm]{' + blkImg + '}};\n'
            #ranges
            if int(row["LowRange"]) > 0:
                 filetext = filetext + '\\node at ( 7 , 4){\\includegraphics[' + iconwidth + ']{' + rangeImg + '}};\n'
                 filetext = filetext + '\\node at (7, 3){' + row["LowRange"] + '};\n'
            print("text")
            #cardtext
            filetext = filetext + "\\node[rectangle, fill opacity = 0.3, fill = white, minimum height =1.5cm, rounded corners = 0.3cm, draw, text width = 4cm]  at (3, 2.5){" + row['Text'] +"};\n" 
            filetext = filetext + "\\end{tikzpicture}\n"
            ofile.write(filetext)