#!/bin/sh
# crop.sh <variant dir> <name> <x0U> <y0U> <x1U> <y1U> : render a region of the sheet at 220 dpi
D=$1; N=$2
R=220
X=$(python3 -c "print(int($3*2.54/25.4*$R))"); Y=$(python3 -c "print(int($4*2.54/25.4*$R))")
W=$(python3 -c "print(int(($5-$3)*2.54/25.4*$R))"); H=$(python3 -c "print(int(($6-$4)*2.54/25.4*$R))")
pdftoppm -png -r $R -x $X -y $Y -W $W -H $H -singlefile "$D"/airfrog-*.pdf "$D/$N"
