#!/bin/bash
# Start desktop app "$@" with the accessibility bus, wait for its new window and bring it
# to the front (full screen, no decorations: the only app visible). Exit 5 if none appears.
set -a; . /tmp/cf/dbus.env 2>/dev/null; set +a
export GTK_MODULES=gail:atk-bridge NO_AT_BRIDGE=0
before=$(xdotool search --onlyvisible --name . 2>/dev/null | sort)
setsid "$@" >/tmp/cf/app.log 2>&1 < /dev/null &
for i in $(seq 100); do
  new=$(comm -13 <(echo "$before") <(xdotool search --onlyvisible --name . 2>/dev/null | sort) | tail -1)
  [ -n "$new" ] && { sleep 0.8; xdotool windowactivate --sync "$new" 2>/dev/null; exit 0; }
  sleep 0.2
done
echo "No window appeared for: $*"; tail -c 300 /tmp/cf/app.log; exit 5
