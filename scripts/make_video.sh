#!/usr/bin/env bash
# One command on a server: source video → your logo instead of the sponsor banner,
# speaker's voice removed (audience laughter kept), English ElevenLabs dub,
# English captions in the original style → rendered MP4.
#
#   export ELEVENLABS_API_KEY=...            # never commit it
#   scripts/make_video.sh input.webm out/result.mp4
#   scripts/make_video.sh https://youtu.be/<id> out/result.mp4   # or a link (downloaded with yt-dlp)
#
# Settings (environment variables, all optional):
#   LOGO=examples/plantogram/logo.png   logo image (PNG with or without transparency, or SVG)
#   LOGO_STYLE=ticker                   ticker: green band with a running ticker that turns like a 3D box
#                                       on the original banner rhythm; float: the logo itself rolls in and floats
#   BRAND=PlantOgram BRAND_URL=plantogram.com.au   ticker text (defaults match the default logo)
#   VOICE=YLbQE9U7P1K6rBNJWNSv          ElevenLabs voice_id
#   TRANSLATION=path.json               ready translation; without it Claude translates (ANTHROPIC_API_KEY)
#   TTS=elevenlabs                      or "mock" for an offline dry run with a test tone
#   WORK=work/<video name>              project directory (reused if it exists; FRESH=1 to redo)
#   KEEP_AMBIENCE=1                     0 = drop the original track completely
#   CAPTIONS=outline                    outline: one white outlined word at a time (default);
#                                       boxes: phrases in solid colour boxes (white on red) + title cards
#   HEADER_PANELS="a.png b.png ..."     images turned through on a 3D box at the top (4 = a box);
#                                       use with LOGO_STYLE=footer to put the logo at the bottom
#   REDUB=0                             a project that is already dubbed only gets its brand block
#                                       rebuilt (no ElevenLabs calls); REDUB=1 translates and dubs again
set -euo pipefail

VIDEO=${1:?"usage: $0 <input video> [output.mp4]"}
OUT=${2:-out/$(basename "${VIDEO%.*}")_en.mp4}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
if [[ -z ${LOGO:-} ]]; then
  LOGO=$ROOT/examples/plantogram/logo.png
  BRAND=${BRAND-PlantOgram}
  BRAND_URL=${BRAND_URL-plantogram.com.au}
fi
LOGO_STYLE=${LOGO_STYLE:-ticker}
CAPTIONS=${CAPTIONS:-outline}
HEADER_PANELS=${HEADER_PANELS:-}
BRAND=${BRAND:-}
BRAND_URL=${BRAND_URL:-}
VOICE=${VOICE:-YLbQE9U7P1K6rBNJWNSv}
TTS=${TTS:-elevenlabs}
[[ -n ${WORK:-} ]] && WORK_SET=1
WORK=${WORK:-$ROOT/work/$(basename "${VIDEO%.*}")}
TRANSLATION=${TRANSLATION:-}
KEEP_AMBIENCE=${KEEP_AMBIENCE:-1}

REDUB=${REDUB:-0}
abs() { python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$1"; }
URL=""
if [[ $VIDEO =~ ^https?:// ]]; then
  URL=$VIDEO
  slug=$(python3 -c 'import re,sys; u=sys.argv[1].split("?v=")[-1].split("/")[-1]; print(re.sub(r"[^A-Za-z0-9_-]+", "_", u)[:40] or "video")' "$URL")
  [[ -n ${WORK_SET:-} ]] || WORK=$ROOT/work/$slug
  [[ -n ${2:-} ]] || OUT=out/${slug}_en.mp4
  VIDEO=$WORK.source.mp4
fi
OUT=$(abs "$OUT"); LOGO=$(abs "$LOGO"); WORK=$(abs "$WORK")
[[ -z $URL ]] && VIDEO=$(abs "$VIDEO")
[[ -n $TRANSLATION ]] && TRANSLATION=$(abs "$TRANSLATION")
say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[[ -n $URL || -f $VIDEO ]] || die "no such video: $VIDEO (copy it to the server, e.g. scp, or pass a https:// link)"
[[ -f $LOGO ]] || die "no such logo: $LOGO"
RESTYLE=0
if [[ -z $HEADER_PANELS && $REDUB != 1 && ${FRESH:-0} != 1 && -f $WORK/assets/voice_en.m4a ]] \
   && grep -q '"logo"' "$WORK/elements.json" 2>/dev/null; then
  RESTYLE=1
fi
if [[ $TTS == elevenlabs && $RESTYLE == 0 ]]; then
  [[ -n ${ELEVENLABS_API_KEY:-} ]] || die "set ELEVENLABS_API_KEY (or TTS=mock for a dry run)"
  (LC_ALL=C; [[ $ELEVENLABS_API_KEY =~ ^[A-Za-z0-9_-]{20,}$ ]]) \
    || die "ELEVENLABS_API_KEY does not look like a key (expected something like sk_..., not the placeholder text)"
fi
if [[ $RESTYLE == 0 && -z $TRANSLATION && -z ${ANTHROPIC_API_KEY:-} && ! -f $WORK/translation.en.json ]]; then
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

if [[ -n $URL && ( ${FRESH:-0} == 1 || ! -f $VIDEO ) ]]; then
  say "downloading $URL"
  pip install -q -U yt-dlp
  mkdir -p "$(dirname "$VIDEO")"
  yt-dlp -f "bv*+ba/b" --merge-output-format mp4 --ffmpeg-location "$ROOT/.bin/ffmpeg" \
    -o "$VIDEO" --force-overwrites "$URL" \
    || die "download failed (YouTube may block server IPs) — copy the file over with scp instead"
fi

if [[ ${FRESH:-0} == 1 || ! -f $WORK/elements.json ]]; then
  say "1/3 extracting layers from $(basename "$VIDEO") (takes ~5-10 min per minute of video)"
  rm -rf "$WORK"
  ex=()
  [[ $CAPTIONS == boxes ]] && ex+=(--caption-boxes)
  python -m vidextract "$VIDEO" -o "$WORK" "${ex[@]}"
else
  say "1/3 reusing extracted layers in $WORK (FRESH=1 to redo)"
fi

brand=()
[[ -n $BRAND ]] && brand+=(--brand "$BRAND")
[[ -n $BRAND_URL ]] && brand+=(--brand-url "$BRAND_URL")
if [[ $RESTYLE == 1 ]]; then
  say "2/3 already dubbed: rebuilding only the brand block ($LOGO_STYLE); REDUB=1 to translate and dub again"
  python -m vidextract restyle "$WORK" --logo-style "$LOGO_STYLE" "${brand[@]}"
else
  say "2/3 logo, voice removal, translation, ElevenLabs dub, English captions"
  args=(localize "$WORK" --logo "$LOGO" --logo-style "$LOGO_STYLE" "${brand[@]}" --voice "$VOICE" --tts "$TTS")
  [[ -n $TRANSLATION ]] && args+=(--translation "$TRANSLATION")
  [[ $KEEP_AMBIENCE == 0 ]] && args+=(--no-ambience)
  if [[ -n $HEADER_PANELS ]]; then
    # shellcheck disable=SC2206  # a space-separated list or a glob, on purpose
    panels=($HEADER_PANELS)
    args+=(--header-panels "${panels[@]}")
  fi
  python -m vidextract "${args[@]}"
fi

say "3/3 rendering"
mkdir -p "$(dirname "$OUT")"
(cd "$WORK" && hyperframes lint . && hyperframes render -o "$OUT")
say "done → $OUT"
