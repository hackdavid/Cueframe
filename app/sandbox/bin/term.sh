#!/bin/bash
# Ensure a visible terminal is open and focused. Its bash records each prompt
# (exit code, timestamp, counter) so long commands can be awaited, and `script`
# logs the output so it can be streamed back.
if ! xdotool search --onlyvisible --class xterm >/dev/null; then
  xterm -geometry 110x28+60+60 -fa Monospace -fs 12 -bg '#111' -fg '#eee' \
    -e script -fq /tmp/cf/term.log -c 'bash --rcfile /opt/cf/bashrc' >/dev/null 2>&1 &
  xdotool search --sync --onlyvisible --class xterm >/dev/null
  for i in $(seq 30); do [ "$(cat /tmp/cf/prompt_n)" != 0 ] && break; sleep 0.2; done
fi
/opt/cf/focus.sh xterm
