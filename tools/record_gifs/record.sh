#!/bin/bash
# Records one scenario to /out/$SCENARIO.gif. Runs inside the recorder container.
set -euo pipefail

HERE=/pptl/tools/record_gifs
export OUT_DIR=/tmp/rec
export DISPLAY=:99
# Without a D-Bus session QGIS skips the GNOME keyring prompt that would block startup.
export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent
export QGIS_PLUGINPATH=/pptl
mkdir -p "$OUT_DIR"

Xvfb "$DISPLAY" -screen 0 1280x800x24 >/dev/null 2>&1 &
sleep 2
openbox >/dev/null 2>&1 &
qgis --nologo --noversioncheck --profiles-path /tmp/profile --code "$HERE/qgis_startup.py" >/tmp/qgis.log 2>&1 &

for _ in $(seq 240); do [ -f "$OUT_DIR/ready" ] || [ -f "$OUT_DIR/startup_error.txt" ] && break; sleep 1; done
if [ ! -f "$OUT_DIR/ready" ]; then
    echo "QGIS never became ready" >&2
    cat "$OUT_DIR/startup_error.txt" 2>/dev/null >&2 || tail -40 /tmp/qgis.log >&2
    exit 1
fi
sleep 3

python3 "$HERE/driver.py" setup
touch "$OUT_DIR/clear_messages"
sleep 1.5

ffmpeg -y -loglevel error -f x11grab -draw_mouse 1 -framerate 15 -video_size 1280x800 -i "$DISPLAY" -c:v libx264 -preset ultrafast -qp 0 "$OUT_DIR/raw.mp4" &
ffmpeg_pid=$!
sleep 1
python3 "$HERE/driver.py" record
sleep 1
kill -INT "$ffmpeg_pid"
wait "$ffmpeg_pid" || true

if [ -s "$OUT_DIR/python_errors.txt" ]; then
    echo "Plugin raised Python errors during $SCENARIO:" >&2
    cat "$OUT_DIR/python_errors.txt" >&2
    exit 1
fi

ffmpeg -y -loglevel error -i "$OUT_DIR/raw.mp4" \
    -vf "fps=12,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=4" \
    "/out/$SCENARIO.gif"
echo "Wrote $SCENARIO.gif"
