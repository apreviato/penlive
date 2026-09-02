#!/bin/sh
# Starts X with a bare window manager and Chromium locked to the local API.
set -e

# -keeptty is required for Xorg's systemd-logind integration.  The service has
# tty1 as its controlling terminal, so keeping it lets logind grant the
# unprivileged penlive user access to the active VT and DRM devices reliably.
#
# -background none stops Xorg painting its default grey weave over the screen
# before any client has drawn. Without it the boot reads as black (Plymouth),
# then grey (this), then the app -- and every later moment the browser is not
# covering the root, including the seconds after "Shut down", shows the same
# grey and looks like a machine that has hung.
exec xinit /opt/penlive/xsession.sh -- :0 vt1 -keeptty -nolisten tcp -background none
