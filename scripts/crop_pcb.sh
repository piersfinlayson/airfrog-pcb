#!/bin/sh
# Render one region of a check_pcb.sh layer PDF at 1200 dpi.
# Arguments: folder, layer render name (top, in2, bottom), x0 y0 x1 y1 in board mm.
D=$1
DPI=${DPI:-1200}
read X Y W H <<EOF
$(python3 -c "d=$DPI/25.4; print(int((100+$3)*d), int((100+$4)*d), int(($5-$3)*d), int(($6-$4)*d))")
EOF
pdftoppm -png -singlefile -r $DPI -x $X -y $Y -W $W -H $H "$D/$2.pdf" "$D/$2-crop"
