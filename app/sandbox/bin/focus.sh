#!/bin/bash
# Bring the app of class $1 to the front; it covers the whole screen, so it is the only one visible.
# If a window of that app (e.g. its file dialog) is already active, leave the focus there.
wins=$(xdotool search --onlyvisible --class "$1")
act=$(xdotool getactivewindow 2>/dev/null)
[ -n "$act" ] && grep -qx "$act" <<< "$wins" && exit 0
wid=$(head -1 <<< "$wins")
[ -n "$wid" ] && xdotool windowactivate --sync "$wid" 2>/dev/null; true
