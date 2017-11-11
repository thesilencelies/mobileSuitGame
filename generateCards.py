#Reads the given CSV of card designs and turns them into TEX files that could be imported as needed

import csv

datafile = 'data.csv'

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
            filetext = filetext + "\\draw[line = blue](1.1,1.9) -- (1.05,1.95)\n"
            #filetext = filetext + '\\node(1,2){\\includegraphics("initiativeImg.png")}\n'
            filetext = filetext + "\\node[circle, minimum width = 1cm, fill opacity = 0.3, fill = white!30blue!70, line = white!40blue!60, draw] (1, 2){}; \\node(1,2){" + row['Initiative'] +"};\n" 
            filetext = filetext + "\\node[rectangle, minimum width = 1cm, fill opacity = 0.3, fill = white!30yellow!70, line = white!40yellow!60, draw] (1, 2){}; \\node(1,2){" + row['Movement'] +"};\n" 
            if row["Basicness"] == "y":
                 filetext = filetext + "\\node[text colour = green]{\\Large{B}};\n"
            #attacks
            if int(row["AttackHigh"]) or int(row["BlockHigh"]):
                filetext = filetext + "\\node[rectangle, minimum height = 1cm, minimum width =2cm, round corners, fill=white, opacity=0.3](4, 2){};\n"
            for d in range(0,int(row["AttackHigh"])):
                filetext = filetext + "\\node(" + str(-i + 7) + ', 1){\\includegraphics{"attackimg.png"}};\n'
            
            #blocks
            for d in range(0,int(row["BlockHigh"])):
                filetext = filetext + "\\node(" + str(i - 7) + ', 2){\\includegraphics{"blockImg.png"}};\n'
            #ranges
             if int(row["RangeHigh"]) > 0:
                 filetext = filetext + '\\node( 7 , 2){\\includegraphics{"rangeimg.png"}};\n'
                 filetext = filetext + '\\node(7, 3){' + row["RangeHigh"] + '};\n'
             
              #attacks
            if int(row["AttackMid"]) or int(row["BlockMid"]):
                filetext = filetext + "\\node[rectangle, minimum height = 1cm, minimum width =2cm, round corners, fill=white, opacity=0.3](4, 5){};\n"
            for d in range(0,int(row["AttackMid"])):
                filetext = filetext + "\\node(" + str(-i + 7) + ', 5){\\includegraphics{"attackimg.png"}};\n'
            
            #blocks
            for d in range(0,int(row["BlockMid"])):
                filetext = filetext + "\\node(" + str(i - 7) + ', 6){\\includegraphics{"blockImg.png"}};\n'
            #ranges
             if int(row["RangeMid"]) > 0:
                 filetext = filetext + '\\node( 7 , 5){\\includegraphics{"rangeimg.png"}};\n'
                 filetext = filetext + '\\node(7, 5){' + row["RangeMid"] + '};\n'
             
              #attacks
            if int(row["AttackLow"]) or int(row["BlockLow"]):
                filetext = filetext + "\\node[rectangle, minimum height = 1cm, minimum width =2cm, round corners, fill=white, opacity=0.3](4, 8){};\n"
            for d in range(0,int(row["AttackLow"])):
                filetext = filetext + "\\node(" + str(-i + 7) + ', 7){\\includegraphics{"attackimg.png"}};\n'
            
            #blocks
            for d in range(0,int(row["BlockLow"])):
                filetext = filetext + "\\node(" + str(i - 7) + ', 8){\\includegraphics{"blockImg.png"}};\n'
            #ranges
             if int(row["RangeLow"]) > 0:
                 filetext = filetext + '\\node( 7 , 7){\\includegraphics{"rangeimg.png"}};\n'
                 filetext = filetext + '\\node(7, 8){' + row["RangeLow"] + '};\n'
             
            #cardtext
            filetext = filetext + "\\node[rectangle, fill opacity = 0.3, fill = white, draw, text width = 5cm] (1, 20){" + row['Text'] +"};\n" 
            filetext = filetext + "\\end{tikzpicture}\n"
            ofile.write(filetext)

