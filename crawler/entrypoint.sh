#!/usr/bin/env bash
# Always provide a virtual display.
#
# Some sites fingerprint the browser and reject headless Chromium outright, so
# individual sources set `crawl.headless: false` in their config. The shell
# cannot read those configs, so gating Xvfb on an env var meant a headed source
# crashed with "no XServer". Xvfb costs a few MB, so just always run it.
set -e

if [ "${DISABLE_XVFB:-false}" != "true" ] && [ -z "${DISPLAY:-}" ]; then
    Xvfb :99 -screen 0 1440x900x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
    export DISPLAY=:99
    for _ in $(seq 1 40); do
        [ -e /tmp/.X11-unix/X99 ] && break
        sleep 0.25
    done
    if [ -e /tmp/.X11-unix/X99 ]; then
        echo "Xvfb ready on :99 (headed browsers work; no visible window)" >&2
    else
        echo "WARNING: Xvfb did not start; headed sources will fail" >&2
        tail -3 /tmp/xvfb.log >&2 || true
    fi
fi

exec "$@"
