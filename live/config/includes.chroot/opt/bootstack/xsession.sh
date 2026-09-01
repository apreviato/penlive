#!/bin/sh
# X session for the kiosk: a minimal WM plus fullscreen Chromium, nothing else.
set -e

openbox &

xset s off
xset -dpms
xset s noblank

exec chromium \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-features=TranslateUI \
    --check-for-update-interval=31536000 \
    --user-data-dir=/var/lib/bootstack/chromium \
    "http://127.0.0.1:7777"
