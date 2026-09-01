#!/bin/sh
# X session for the kiosk: locked-down WM plus fullscreen Chromium, nothing else.
set -e

API_URL="http://127.0.0.1:7777"
PROFILE_DIR=/var/lib/bootstack/chromium

# -dontzap is an Xorg server flag, but Ctrl+Alt+Backspace is also controlled at
# the keymap level; clearing the Terminate action removes the "kill X" escape.
setxkbmap -option "" 2>/dev/null || true

# No screen blanking: the kiosk is often left showing download progress, and a
# black screen reads as a crashed machine.
xset s off
xset -dpms
xset s noblank

# Hide the pointer after a moment of inactivity; there is nothing to point at
# outside the app.
command -v unclutter >/dev/null 2>&1 && unclutter -idle 3 &

openbox --config-file /opt/bootstack/openbox-rc.xml &
OPENBOX_PID=$!

# Restarting Chromium in a loop is what makes the kiosk unescapable: if a user
# somehow closes the window, the app comes straight back instead of leaving a
# bare X root window with no way out.
while true; do
    # Clear exit flags so a previous unclean shutdown does not raise the
    # "Restore pages?" bubble, which is itself a way out of the app.
    if [ -f "${PROFILE_DIR}/Default/Preferences" ]; then
        sed -i 's/"exit_type":"Crashed"/"exit_type":"Normal"/; s/"exited_cleanly":false/"exited_cleanly":true/' \
            "${PROFILE_DIR}/Default/Preferences" 2>/dev/null || true
    fi

    chromium \
        --kiosk \
        --app="${API_URL}" \
        --user-data-dir="${PROFILE_DIR}" \
        --no-first-run \
        --no-default-browser-check \
        --noerrdialogs \
        --disable-infobars \
        --disable-session-crashed-bubble \
        --disable-features=TranslateUI,Translate \
        --disable-pinch \
        --overscroll-history-navigation=0 \
        --disable-dev-shm-usage \
        --check-for-update-interval=31536000 \
        --autoplay-policy=no-user-gesture-required \
        --password-store=basic \
        || true

    # Chromium exited (crash, or someone found a way to close it). Pause briefly
    # so a hard crash-loop doesn't spin the CPU, then bring the app back.
    sleep 2
done

kill "${OPENBOX_PID}" 2>/dev/null || true
