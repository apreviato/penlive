#!/bin/sh
# Starts X with a bare window manager and Chromium locked to the local API.
set -e

# -keeptty is required for Xorg's systemd-logind integration.  The service has
# tty1 as its controlling terminal, so keeping it lets logind grant the
# unprivileged penlive user access to the active VT and DRM devices reliably.
exec xinit /opt/penlive/xsession.sh -- :0 vt1 -keeptty -nolisten tcp
