#!/usr/bin/env bash
# The speech pod's entrypoint: start VoiceStudio, then warm VoxCPM2 before the pod reports ready.
#
# VoiceStudio's /health proves the app is up, not the engine. VoxCPM2's weights download on first
# use, and a cloned voice's first call recompiles the engine, which takes well over ten seconds in a
# fresh process. So, as VoiceStudio advises before admitting traffic, this script pre-fetches the
# weights and then warms the engine: it speaks a sentence, clones that clip and speaks as the clone.
# Only then does it write /tmp/warm, which the chart's readiness probe waits for.
# https://github.com/debpalash/VoiceStudio/blob/v0.5.6/docs/production-private-api.md
# Its calls come from 127.0.0.1, which VoiceStudio lets through without the API key:
# https://github.com/debpalash/VoiceStudio/blob/v0.5.6/backend/main.py (BearerKeyMiddleware)
set -euo pipefail

api=http://127.0.0.1:3900
repo=openbmb/VoxCPM2
sentence="Good morning. The northern seas are calm today, and the ship is ready to sail."

# VoiceStudio's own command, the base image's entrypoint, in the background.
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 3900 &
server=$!
# As PID 1 this script would ignore the pod's SIGTERM, so pass it on.
trap 'kill -TERM "$server"; wait "$server"' TERM INT

echo "warm-up: waiting for VoiceStudio to start"
until curl -fs -o /dev/null "$api/health"; do  # 503 until its startup finishes
  kill -0 "$server"  # stop here if VoiceStudio has exited
  sleep 2
done

echo "warm-up: downloading $repo"
# The install answers at once and reports its outcome on the download-progress stream, so listen
# there first: its last event cannot then slip past.
python3 - "$api" "$repo" <<'EOF'
import json, sys, urllib.request

api, repo = sys.argv[1:]
stream = urllib.request.urlopen(f"{api}/setup/download-stream", timeout=120)  # keep-alives every 30 s
install = urllib.request.Request(
    f"{api}/models/install",
    data=json.dumps({"repo_id": repo}).encode(),
    headers={"Content-Type": "application/json"},
)
print(json.load(urllib.request.urlopen(install)))
for line in stream:
    if not line.startswith(b"data:"):
        continue  # blank lines and ": keepalive" comments
    event = json.loads(line[len(b"data:"):])
    if event.get("repo_id") != repo:
        continue
    if event.get("phase") == "install_done":
        sys.exit(0)
    if event.get("phase") in ("install_error", "install_cancelled"):
        sys.exit(f"warm-up: the download failed: {event}")
sys.exit("warm-up: the progress stream closed before the download finished")
EOF

# Always model "voxcpm2", as the bot asks: any other id is served by a separate engine instance.
echo "warm-up: speaking a sentence"
curl -fsS "$api/v1/audio/speech" -H "Content-Type: application/json" -o /tmp/warm-up.wav \
  -d "{\"model\": \"voxcpm2\", \"input\": \"$sentence\", \"response_format\": \"wav\"}"

echo "warm-up: cloning that voice, then speaking in it"
voice=$(curl -fsS "$api/profiles" -F name=warm-up -F kind=clone --form-string "ref_text=$sentence" \
  -F "ref_audio=@/tmp/warm-up.wav;type=audio/wav" | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
curl -fsS "$api/v1/audio/speech" -H "Content-Type: application/json" -o /dev/null \
  -d "{\"model\": \"voxcpm2\", \"input\": \"$sentence\", \"voice\": \"$voice\", \"response_format\": \"wav\"}"
curl -fsS -X DELETE "$api/profiles/$voice" -o /dev/null
rm /tmp/warm-up.wav

touch /tmp/warm
echo "warm-up: done"
wait "$server"
