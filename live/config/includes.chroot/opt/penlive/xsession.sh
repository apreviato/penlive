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

# Wait for Openbox to actually own the screen before the browser maps its
# window. A window mapped before any WM is running stays unmanaged: it never
# receives focus and never gets _NET_WM_STATE_FULLSCREEN, which is what left
# the kiosk sitting behind an unfocused root window — a black screen with a
# working cursor, while Chromium underneath still answered keyboard shortcuts.
wait_for_window_manager() {
    command -v xprop >/dev/null 2>&1 || { sleep 1; return 0; }
    i=0
    while [ "${i}" -lt 100 ]; do
        if xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null | grep -q '0x'; then
            return 0
        fi
        i=$((i + 1))
        sleep 0.1
    done
}

# Second line of defence for "the app must be the thing on screen". Openbox has
# no Alt+Tab and the kiosk window has no titlebar, so if anything ever does end
# up in front of the manager the user has no way to get back to it. This puts
# the kiosk window back on top and back in focus instead of waiting for help.
keep_kiosk_in_front() {
    browser_pid=$1
    command -v xdotool >/dev/null 2>&1 || return 0
    while kill -0 "${browser_pid}" 2>/dev/null; do
        # The kiosk window is the first one this Chromium maps; anything later
        # is the intruder we are covering up.
        # --class selects which property to match; the pattern is positional, so
        # dropping it makes xdotool reject the whole call. Matching the class as
        # well as the pid keeps this working on builds where _NET_WM_PID is
        # missing and --pid quietly filters nothing.
        kiosk_window=$(xdotool search --onlyvisible --pid "${browser_pid}" --class '[Cc]hromium' 2>/dev/null | head -n 1)
        if [ -n "${kiosk_window}" ]; then
            active_window=$(xdotool getactivewindow 2>/dev/null || true)
            if [ "${active_window}" != "${kiosk_window}" ]; then
                xdotool windowactivate "${kiosk_window}" 2>/dev/null || true
                xdotool windowraise "${kiosk_window}" 2>/dev/null || true
            fi
        fi
        sleep 2
    done
}

# Restarting Chromium in a loop is what makes the kiosk unescapable: if a user
# somehow closes the window, the app comes straight back instead of leaving a
# bare X root window with no way out. Chromium opens a local loading screen
# immediately; that page replaces itself with the manager as soon as the API
# answers, so boot never exposes a white browser surface or an error page.
while true; do
    wait_for_window_manager

    # A profile left locked by a killed Chromium makes the next launch hand its
    # URL to a process that no longer draws anything and exit 0 straight away,
    # spinning this loop while the screen stays black.
    rm -f "${PROFILE_DIR}/Singleton"* 2>/dev/null || true

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
        --disable-extensions \
        --disable-notifications \
        --overscroll-history-navigation=0 \
        --disable-dev-shm-usage \
        --default-background-color=050607 \
        --force-dark-mode \
        --check-for-update-interval=31536000 \
        --autoplay-policy=no-user-gesture-required \
        --start-fullscreen \
        --window-position=0,0 \
        --disable-search-engine-choice-screen \
        "${LOADING_URL}" &
    CHROMIUM_PID=$!

    keep_kiosk_in_front "${CHROMIUM_PID}" &
    FOREGROUND_PID=$!

    wait "${CHROMIUM_PID}" || true
    kill "${FOREGROUND_PID}" 2>/dev/null || true

    # Chromium exited (crash, or someone found a way to close it). Pause briefly
    # so a hard crash-loop doesn't spin the CPU, then bring the app back.
    sleep 2
done

kill "${OPENBOX_PID}" 2>/dev/null || true
