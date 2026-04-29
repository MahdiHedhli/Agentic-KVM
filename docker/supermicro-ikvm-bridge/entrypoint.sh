#!/bin/sh
set -eu

export DISPLAY="${DISPLAY:-:1}"
export VNC_PORT="${VNC_PORT:-5900}"
export NOVNC_PORT="${NOVNC_PORT:-6080}"
export GEOMETRY="${GEOMETRY:-1440x900x24}"
export LOG_DIR="${LOG_DIR:-/session/bridge-logs}"

mkdir -p /tmp/.X11-unix
mkdir -p "$LOG_DIR"

Xvfb "$DISPLAY" -screen 0 "$GEOMETRY" >"$LOG_DIR/xvfb.log" 2>&1 &
XVFB_PID=$!

fluxbox >"$LOG_DIR/fluxbox.log" 2>&1 &
FLUXBOX_PID=$!

x11vnc -display "$DISPLAY" -rfbport "$VNC_PORT" -forever -shared -nopw >"$LOG_DIR/x11vnc.log" 2>&1 &
X11VNC_PID=$!

websockify --web=/usr/share/novnc/ "$NOVNC_PORT" "127.0.0.1:${VNC_PORT}" >"$LOG_DIR/websockify.log" 2>&1 &
WEBSOCKIFY_PID=$!

javaws /session/ikvm.jnlp >"$LOG_DIR/javaws.log" 2>&1 &
JAVAWS_PID=$!

trap 'kill "$JAVAWS_PID" "$WEBSOCKIFY_PID" "$X11VNC_PID" "$FLUXBOX_PID" "$XVFB_PID" 2>/dev/null || true' INT TERM EXIT

while kill -0 "$XVFB_PID" 2>/dev/null && kill -0 "$X11VNC_PID" 2>/dev/null && kill -0 "$WEBSOCKIFY_PID" 2>/dev/null; do
    sleep 5
done
