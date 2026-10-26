#!/bin/bash
# Smooth, human-looking pointer move to ($1,$2) over ~300 ms.
eval $(xdotool getmouselocation --shell)
for i in $(seq 1 12); do
  xdotool mousemove $(( X + ($1 - X) * i / 12 )) $(( Y + ($2 - Y) * i / 12 )); sleep 0.025
done
