#!/usr/bin/env bash
set -euo pipefail
mkdir -p /data/jobs /data/browser /tmp/pulse
chmod 700 /tmp/pulse
export DISPLAY="${DISPLAY:-:99}"
export PULSE_SERVER=unix:/tmp/pulse/native
export XDG_RUNTIME_DIR=/tmp/pulse
Xvfb "$DISPLAY" -screen 0 "${CAPTURE_WIDTH:-1280}x${CAPTURE_HEIGHT:-720}x24" -nolisten tcp &
x_pid=$!
pulseaudio --daemonize=no --exit-idle-time=-1 --disallow-exit --log-target=stderr \
  -n --load='module-native-protocol-unix socket=/tmp/pulse/native auth-anonymous=1' \
  --load='module-null-sink sink_name=recording sink_properties=device.description=Recording' &
pulse_pid=$!
for attempt in $(seq 1 50); do
  if pactl info >/dev/null 2>&1 && xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then break; fi
  sleep 0.2
done
pactl set-default-sink recording
pactl set-default-source recording.monitor
# Chromium fullscreen needs a window manager to move/resize its native window.
openbox --sm-disable &
wm_pid=$!
for attempt in $(seq 1 50); do
  if [[ "$(xprop -root _NET_SUPPORTING_WM_CHECK)" == *"window id"* ]]; then break; fi
  sleep 0.2
done
# VNC's protocol uses only the first eight password characters. Kept localhost-only.
x11vnc -storepasswd "$VNC_PASSWORD" /tmp/vnc-pass >/dev/null
x11vnc -display "$DISPLAY" -rfbauth /tmp/vnc-pass -rfbport 5900 -localhost -forever -shared -quiet &
vnc_pid=$!
websockify --web=/usr/share/novnc 0.0.0.0:6080 localhost:5900 &
web_pid=$!
uvicorn capture.app:app --host 0.0.0.0 --port 8000 --no-proxy-headers &
app_pid=$!
cleanup() {
  trap - EXIT TERM INT
  kill -TERM "$app_pid" 2>/dev/null || true
  wait "$app_pid" || true
  kill "$web_pid" "$vnc_pid" "$wm_pid" "$pulse_pid" "$x_pid" 2>/dev/null || true
}
trap cleanup EXIT TERM INT
# A failed display, audio server, or VNC gateway makes the container unhealthy.
wait -n "$app_pid" "$x_pid" "$pulse_pid" "$vnc_pid" "$web_pid" "$wm_pid"
