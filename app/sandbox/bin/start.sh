#!/bin/bash
# Boot the virtual desktop: Xvfb :99, fluxbox, VNC + noVNC (live view), Chromium.
W=${CF_W:-1280}; H=${CF_H:-720}
mkdir -p /tmp/cf; echo 0 > /tmp/cf/prompt_n
Xvfb :99 -screen 0 ${W}x${H}x24 -nolisten tcp & sleep 1
export DISPLAY=:99
# Session bus + accessibility bus, so desktop apps expose their buttons and fields (AT-SPI).
eval "$(dbus-launch --sh-syntax)"; echo "DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS" > /tmp/cf/dbus.env
/usr/libexec/at-spi-bus-launcher --launch-immediately >/dev/null 2>&1 &
mkdir -p ~/.fluxbox && cp /opt/cf/fluxbox/init /opt/cf/fluxbox/apps ~/.fluxbox/
fluxbox >/dev/null 2>&1 &
x11vnc -display :99 -forever -shared -viewonly -nopw -quiet -rfbport 5900 >/dev/null 2>&1 &
websockify --web /usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 &
chromium --no-sandbox --test-type --disable-gpu --disable-dev-shm-usage --no-first-run \
  --no-default-browser-check --disable-infobars --window-size=${W},${H} --window-position=0,0 \
  --force-device-scale-factor=${CF_SCALE:-1} --hide-crash-restore-bubble --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --remote-allow-origins=* --disable-features=Translate \
  "${CF_START_URL:-about:blank}" >/tmp/chromium.log 2>&1 &
wait
