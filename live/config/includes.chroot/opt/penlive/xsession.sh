#!/bin/sh
# X session for the kiosk: locked-down WM plus fullscreen Chromium, nothing else.
set -e

LOADING_URL="file:///opt/penlive/loading.html"
# A fresh runtime profile prevents Chromium from restoring an old blank or
# crashed window over the kiosk. Persistent settings live in PenLive's backend,
# so the browser profile itself does not need to survive a reboot.
PROFILE_DIR=/run/penlive-kiosk/chromium
export HOME=/var/lib/penlive
export XAUTHORITY=/var/lib/penlive/.Xauthority
install -d -m 0700 "${PROFILE_DIR}"

# Paint the X root immediately. This removes the black/white flashes while the
# API and browser finish starting.
command -v xsetroot >/dev/null 2>&1 && xsetroot -solid '#050607' || true

# -dontzap is an Xorg server flag, but Ctrl+Alt+Backspace is also controlled at
# the keymap level; clearing the Terminate action removes the "kill X" escape.
setxkbmap -option "" 2>/dev/null || true

# No screen blanking: the kiosk is often left showing download progress, and a
# black screen reads as a crashed machine.
# These are preferences, not prerequisites.  In particular, framebuffer and
# some fallback drivers do not expose the DPMS extension; xset then exits
# non-zero and, with `set -e`, used to tear down X and trigger a visible
# three-second restart/flicker loop.
xset s off 2>/dev/null || true
xset -dpms 2>/dev/null || true
xset s noblank 2>/dev/null || true

# Hide the pointer after a moment of inactivity; there is nothing to point at
# outside the app.
command -v unclutter >/dev/null 2>&1 && unclutter -idle 3 &

openbox --config-file /opt/penlive/openbox-rc.xml &
OPENBOX_PID=$!

# Restarting Chromium in a loop is what makes the kiosk unescapable: if a user
# somehow closes the window, the app comes straight back instead of leaving a
# bare X root window with no way out. Chromium opens a local loading screen
# immediately; that page replaces itself with the manager as soon as the API
# answers, so boot never exposes a white browser surface or an error page.
while true; do
    chromium \
        --kiosk \
        --user-data-dir="${PROFILE_DIR}" \
        --no-first-run \
        --no-default-browser-check \
        --noerrdialogs \
        --disable-infobars \
        --disable-session-crashed-bubble \
        --disable-features=TranslateUI,Translate,InfiniteSessionRestore \
        --disable-component-update \
        --disable-restore-session-state \
        --disable-background-networking \
        --disable-sync \
        --disable-save-password-bubble \
        --disable-password-generation \
        --disable-pinch \
        --overscroll-history-navigation=0 \
        --disable-dev-shm-usage \
        --default-background-color=050607 \
        --force-dark-mode \
        --check-for-update-interval=31536000 \
        --autoplay-policy=no-user-gesture-required \
        --incognito \
        "${LOADING_URL}" \
        || true

    # Chromium exited (crash, or someone found a way to close it). Pause briefly
    # so a hard crash-loop doesn't spin the CPU, then bring the app back.
    sleep 2
done

kill "${OPENBOX_PID}" 2>/dev/null || true
