#!/bin/bash
# Bring the app of class $1 to the front; it covers the whole screen, so it is the only one visible.
wid=$(xdotool search --onlyvisible --class "$1" | head -1)
[ -n "$wid" ] && xdotool windowactivate --sync "$wid" 2>/dev/null; true
