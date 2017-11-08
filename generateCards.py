#Reads the given CSV of card designs and turns them into TEX files that could be imported as needed

import csv

datafile = 'data.csv'

outputfolder='gundamCards/objects/card_'
with open(datafile) as csvfile:
     reader = csv.DictReader(csvfile)
     i = 0
     for row in reader:
         #CONSTRUCT A NEW CARD
         with open(outputfolder + str(i) + '.tex') as ofile:
            i = i + 1
            #background image should be loaded here?
            filetext = "\\begin{tikzpicture}[]\n "
            #format the card
             #default symbols
             #lightning motif
             filetext = filetext + "\\draw()"
             filetext = filetext + "\\node[circle, minimum width = 1cm, fill opacity = 0.3, fill = white!30blue!70, line =white!40blue!60, draw] (1, 2){" + row['inititive'] +"}" 
            
            if row["isBasic"] == "y":
                 filetext = filtext + "\\node "
             #attacks
             
             #ranges
             
             #blocks
             
             #cardtext - needs tex commands to make it multilines
             filetext = filetext + "\\node[rectangle, fill opacity = 0.3, fill = white, draw, text width = 5cm] (1, 20){" + row['text'] +"}" 
            ofile.write(filetext)
             print(row['first_name'], row['last_name'])