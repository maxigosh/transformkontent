#!/usr/bin/env bash
# One command on a server: source video → your logo instead of the sponsor banner,
# speaker's voice removed (audience laughter kept), English ElevenLabs dub,
# English captions in the original style → rendered MP4.
#
#   export ELEVENLABS_API_KEY=...            # never commit it
#   scripts/make_video.sh input.webm out/result.mp4
#
# Settings (environment variables, all optional):
#   LOGO=examples/plantogram/logo.png   logo image (PNG with or without transparency, or SVG)
#   VOICE=YLbQE9U7P1K6rBNJWNSv          ElevenLabs voice_id
#   TRANSLATION=path.json               ready translation; without it Claude translates (ANTHROPIC_API_KEY)
#   TTS=elevenlabs                      or "mock" for an offline dry run with a test tone
#   WORK=work/<video name>              project directory (reused if it exists; FRESH=1 to redo)
#   KEEP_AMBIENCE=1                     0 = drop the original track completely
set -euo pipefail

VIDEO=${1:?"usage: $0 <input video> [output.mp4]"}
OUT=${2:-out/$(basename "${VIDEO%.*}")_en.mp4}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
LOGO=${LOGO:-$ROOT/examples/plantogram/logo.png}
VOICE=${VOICE:-YLbQE9U7P1K6rBNJWNSv}
TTS=${TTS:-elevenlabs}
WORK=${WORK:-$ROOT/work/$(basename "${VIDEO%.*}")}
TRANSLATION=${TRANSLATION:-}
KEEP_AMBIENCE=${KEEP_AMBIENCE:-1}

abs() { python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$1"; }
VIDEO=$(abs "$VIDEO"); OUT=$(abs "$OUT"); LOGO=$(abs "$LOGO"); WORK=$(abs "$WORK")
[[ -n $TRANSLATION ]] && TRANSLATION=$(abs "$TRANSLATION")
say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[[ -f $VIDEO ]] || die "no such video: $VIDEO"
[[ -f $LOGO ]] || die "no such logo: $LOGO"
if [[ $TTS == elevenlabs && -z ${ELEVENLABS_API_KEY:-} ]]; then
  die "set ELEVENLABS_API_KEY (or TTS=mock for a dry run)"
fi
if [[ -z $TRANSLATION && -z ${ANTHROPIC_API_KEY:-} && ! -f $WORK/translation.en.json ]]; then
  die "set ANTHROPIC_API_KEY for automatic translation, or TRANSLATION=path/to/translation.json"
fi

cd "$ROOT"

say "dependencies"
command -v node >/dev/null || die "Node.js >= 22 is required"
if [[ ! -d .venv ]]; then python3 -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt
npm install --silent --no-audit --no-fund

# ffmpeg/ffprobe for HyperFrames: system ones if present, otherwise the bundled builds
FFMPEG=$(command -v ffmpeg || python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')
FFPROBE=$(command -v ffprobe || node -e 'console.log(require("@ffprobe-installer/ffprobe").path)')
mkdir -p .bin && ln -sf "$FFMPEG" .bin/ffmpeg && ln -sf "$FFPROBE" .bin/ffprobe
export PATH="$ROOT/.bin:$ROOT/node_modules/.bin:$PATH"
export HYPERFRAMES_FFMPEG_PATH="$ROOT/.bin/ffmpeg" HYPERFRAMES_FFPROBE_PATH="$ROOT/.bin/ffprobe"
export HYPERFRAMES_NO_TELEMETRY=1 HYPERFRAMES_NO_UPDATE_CHECK=1
if [[ -z ${HYPERFRAMES_BROWSER_PATH:-} ]]; then hyperframes browser ensure; fi

if [[ ${FRESH:-0} == 1 || ! -f $WORK/elements.json ]]; then
  say "1/3 extracting layers from $(basename "$VIDEO") (takes ~5-10 min per minute of video)"
  rm -rf "$WORK"
  python -m vidextract "$VIDEO" -o "$WORK"
else
  say "1/3 reusing extracted layers in $WORK (FRESH=1 to redo)"
fi

say "2/3 logo, voice removal, translation, ElevenLabs dub, English captions"
args=(localize "$WORK" --logo "$LOGO" --voice "$VOICE" --tts "$TTS")
[[ -n $TRANSLATION ]] && args+=(--translation "$TRANSLATION")
[[ $KEEP_AMBIENCE == 0 ]] && args+=(--no-ambience)
python -m vidextract "${args[@]}"

say "3/3 rendering"
mkdir -p "$(dirname "$OUT")"
(cd "$WORK" && hyperframes lint . && hyperframes render -o "$OUT")
say "done → $OUT"
