#!/bin/bash
# Raise and focus the first window of class $1.
wid=$(xdotool search --onlyvisible --class "$1" | head -1)
[ -n "$wid" ] && xdotool windowactivate --sync "$wid" 2>/dev/null; true
