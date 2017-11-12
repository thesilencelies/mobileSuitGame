#Reads the given CSV of card designs and turns them into TEX files that could be imported as needed

import csv

datafile = 'data.csv'

mAtkImg = ''
rAtkImg = ''
blkImg = ''
rangeImg = ''
initImg = ''
mvImg = ''

outputfolder='objects/card_'
with open(datafile) as csvfile:
     reader = csv.DictReader(csvfile)
     i = 0
     for row in reader:
         #CONSTRUCT A NEW CARD
         with open(outputfolder + str(i) + '.tex', 'w') as ofile:
            i = i + 1
            #background image should be loaded here?
            filetext = "\\begin{tikzpicture}[scale=0.5]\n "
            #format the card
            #default symbols
            #lightning motif
            filetext = filetext + "\\draw[blue](1.1,1.9) -- (1.05,1.95)\n"
            #filetext = filetext + '\\node(1,2){\\includegraphics("initiativeImg.png")}\n'
            filetext = filetext + "\\node[circle, minimum width = 1cm, fill opacity = 0.3, fill = white!30blue!70, line = white!40blue!60, draw] (1, 2){}; \\node(1,2){" + row['Initiative'] +"};\n" 
            filetext = filetext + "\\node[rectangle, minimum width = 1cm, fill opacity = 0.3, fill = white!30yellow!70, line = white!40yellow!60, draw] (1, 2){}; \\node(1,2){" + row['Movement'] +"};\n" 
            if row["Basicness"] == "y":
                 filetext = filetext + "\\node[text colour = green]{\\Large{B}};\n"
            #attacks
            if int(row["HighAttack"]) or int(row["HighBlock"]):
                filetext = filetext + "\\node[rectangle, minimum height = 1cm, minimum width =2cm, round corners, fill=white, opacity=0.3](4, 2){};\n"
            for d in range(0,int(row["HighAttack"])):
                filetext = filetext + "\\node(" + str(-i + 7) + ', 1){\\includegraphics{"attackimg.png"}};\n'
            
            #blocks
            for d in range(0,int(row["HighBlock"])):
                filetext = filetext + "\\node(" + str(i - 7) + ', 2){\\includegraphics{"blockImg.png"}};\n'
            #ranges
            if int(row["HighRange"]) > 0:
                 filetext = filetext + '\\node( 7 , 2){\\includegraphics{"rangeimg.png"}};\n'
                 filetext = filetext + '\\node(7, 3){' + row["HighRange"] + '};\n'
             
              #attacks
            if int(row["MidAttack"]) or int(row["MidBlock"]):
                filetext = filetext + "\\node[rectangle, minimum height = 1cm, minimum width =2cm, round corners, fill=white, opacity=0.3](4, 5){};\n"
            for d in range(0,int(row["MidAttack"])):
                filetext = filetext + "\\node(" + str(-i + 7) + ', 5){\\includegraphics{"attackimg.png"}};\n'
            
            #blocks
            for d in range(0,int(row["MidBlock"])):
                filetext = filetext + "\\node(" + str(i - 7) + ', 6){\\includegraphics{"blockImg.png"}};\n'
            #ranges
            if int(row["MidRange"]) > 0:
                 filetext = filetext + '\\node( 7 , 5){\\includegraphics{"rangeimg.png"}};\n'
                 filetext = filetext + '\\node(7, 5){' + row["MidRange"] + '};\n'
             
              #attacks
            if int(row["LowAttack"]) or int(row["LowBlock"]):
                filetext = filetext + "\\node[rectangle, minimum height = 1cm, minimum width =2cm, round corners, fill=white, opacity=0.3](4, 8){};\n"
            for d in range(0,int(row["LowAttack"])):
                filetext = filetext + "\\node(" + str(-i + 7) + ', 7){\\includegraphics{"attackimg.png"}};\n'
            
            #blocks
            for d in range(0,int(row["LowBlock"])):
                filetext = filetext + "\\node(" + str(i - 7) + ', 8){\\includegraphics{"blockImg.png"}};\n'
            #ranges
            if int(row["LowRange"]) > 0:
                 filetext = filetext + '\\node( 7 , 7){\\includegraphics{"rangeimg.png"}};\n'
                 filetext = filetext + '\\node(7, 8){' + row["LowRange"] + '};\n'
             
            #cardtext
            filetext = filetext + "\\node[rectangle, fill opacity = 0.3, fill = white, draw, text width = 5cm] (1, 20){" + row['Text'] +"};\n" 
            filetext = filetext + "\\end{tikzpicture}\n"
            ofile.write(filetext)

