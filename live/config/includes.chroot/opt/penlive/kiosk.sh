#!/bin/sh
# Starts X with a bare window manager and Chromium locked to the local API.
set -e

API_URL="http://127.0.0.1:7777"

# Don't paint a browser error page before uvicorn finishes binding.
for _ in $(seq 1 60); do
    if curl -sf "${API_URL}/api/health" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

exec xinit /opt/penlive/xsession.sh -- :0 vt1 -nolisten tcp
