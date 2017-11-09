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
            filetext = "\\begin{tikzpicture}[]\n "
            #format the card
            #default symbols
            #lightning motif
            filetext = filetext + "\\draw[line = blue](1.1,1.9) -- (1.05,1.95)\n"
            filetext = filetext + "\\node[circle, minimum width = 1cm, fill opacity = 0.3, fill = white!30blue!70, line = white!40blue!60, draw] (1, 2){" + row['Initiative'] +"};\n" 
            
            if row["Basicness"] == "y":
                 filetext = filetext + "\\node[text colour = green]{\\Large{B}};\n"
            #attacks
            

            #ranges
             
            #blocks
             
            #cardtext - needs tex commands to make it multilines
            filetext = filetext + "\\node[rectangle, fill opacity = 0.3, fill = white, draw, text width = 5cm] (1, 20){" + row['Text'] +"};\n" 
            filetext = filetext + "\\end{tikzpicture}\n"
            ofile.write(filetext)

