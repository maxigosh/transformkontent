"""Write the extracted layers as a HyperFrames project (index.html + assets)."""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent
HYPERFRAMES_VERSION = "0.8.96"
FONT_FAMILY = "Rubik"
FONT_CAP_RATIO = 0.70   # Rubik: capHeight 700 / unitsPerEm 1000


def _vendor_gsap(dst: Path) -> str:
    """Copy GSAP next to the project so renders don't depend on a CDN."""
    for cand in (REPO / "node_modules" / "gsap" / "dist" / "gsap.min.js",):
        if cand.exists():
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy(cand, dst / "gsap.min.js")
            return "assets/vendor/gsap.min.js"
    return "https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"


def _caption_css(style: dict, W: int, H: int) -> tuple[str, int, int]:
    cap_h = style["cap_height_frac"] * H
    font_px = max(8, round(cap_h / FONT_CAP_RATIO))
    box_h = round(font_px * 1.8)
    top = round(style["center"]["y"] * H - box_h / 2)
    # x centre: shift the full-width box so the word centre lands where it was measured
    dx = round((style["center"]["x"] - 0.5) * W)
    stroke = round(style["stroke_px"] * 2, 1)
    css = f"""
      .caption-word {{
        position: absolute;
        left: {dx}px;
        width: {W}px;
        top: {top}px;
        height: {box_h}px;
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10;
        pointer-events: none;
      }}
      .caption-text {{
        display: block;
        font-family: "{FONT_FAMILY}", sans-serif;
        font-weight: 900;
        font-size: {font_px}px;
        line-height: 1;
        color: {style["text_color"]};
        -webkit-text-stroke: {stroke}px {style["stroke_color"]};
        paint-order: stroke fill;
        {"text-transform: uppercase;" if style.get("uppercase") else ""}
        white-space: nowrap;
        max-width: {round(W * 0.84)}px;
      }}"""
    return css, font_px, box_h


def _logo_markup(logo: dict, W: int) -> tuple[str, str]:
    z = logo["zone_px"]
    zone_h = z[3] - z[1]
    max_w = W * logo.get("max_width_pct", 80) / 100
    max_h = zone_h * logo.get("max_height_pct", 62) / 100
    aspect = logo.get("aspect") or max_w / max_h
    bw = round(min(max_w, max_h * aspect))
    bh = round(bw / aspect)
    left, top = (W - bw) // 2, z[1] + (zone_h - bh) // 2
    mark, word = logo.get("mark"), logo.get("word")
    if mark and word:
        parts = f"""
            <div id="logo-mark" style="left: {mark["left"] * 100:.3f}%; width: {mark["width"] * 100:.3f}%">
              <div id="logo-mark-spin"><img src="{mark["src"]}" alt="" /></div>
            </div>
            <div id="logo-word-clip" style="left: {word["left"] * 100:.3f}%; width: {word["width"] * 100:.3f}%">
              <img id="logo-word" src="{word["src"]}" alt="" />
            </div>"""
    else:
        parts = f"""
            <div id="logo-mark" style="left: 0; width: 100%">
              <div id="logo-mark-spin"><img src="{logo["src"]}" alt="" /></div>
            </div>"""
    css = f"""
      #logo-wrap {{
        position: absolute;
        left: {left}px;
        top: {top}px;
        width: {bw}px;
        height: {bh}px;
        z-index: 5;
      }}
      #logo-float {{
        position: absolute;
        inset: 0;
        filter: drop-shadow(0 0 3px rgba(255, 255, 255, 0.55)) drop-shadow(0 0 16px rgba(255, 255, 255, 0.28))
          drop-shadow(0 8px 16px rgba(0, 0, 0, 0.5));
      }}
      #logo-mark,
      #logo-word-clip {{
        position: absolute;
        top: 0;
        height: 100%;
      }}
      #logo-word-clip {{
        overflow: hidden;
      }}
      #logo-mark-spin,
      #logo-mark-spin img,
      #logo-word {{
        display: block;
        width: 100%;
        height: 100%;
        object-fit: contain;
      }}
      #logo-shine-layer {{
        position: absolute;
        inset: 0;
        overflow: hidden;
        pointer-events: none;
        -webkit-mask: url("{logo["src"]}") center / 100% 100% no-repeat;
        mask: url("{logo["src"]}") center / 100% 100% no-repeat;
      }}
      #logo-shine {{
        position: absolute;
        top: 0;
        left: 0;
        width: 22%;
        height: 100%;
        background: linear-gradient(105deg, rgba(255, 255, 255, 0) 0%, rgba(255, 255, 255, 0.65) 50%,
          rgba(255, 255, 255, 0) 100%);
      }}"""
    markup = f"""      <!-- layer 1: brand logo (replaces the extracted overlay): the mark rolls in, the wordmark
           slides out, the logo floats, a highlight sweeps across it, and the mark spins on the
           original banner-change rhythm -->
      <div id="logo-wrap">
        <div id="logo-float">{parts}
          <div id="logo-shine-layer"><div id="logo-shine"></div></div>
        </div>
      </div>"""
    return css, markup


def _logo_script(logo: dict | None, duration: float) -> str:
    if not logo:
        return ""
    beats = logo.get("beats") or [0.0]
    split = bool(logo.get("mark") and logo.get("word"))
    n_float = max(1, int(duration / 1.2))
    n_wobble = max(1, int(duration / 1.6))
    intro = """
      // intro: the mark rolls in, the wordmark slides out from behind it
      tl.fromTo("#logo-mark-spin", { scale: 0, rotation: -300 },
        { scale: 1, rotation: 0, duration: 0.9, ease: "back.out(1.6)" }, 0);
      tl.fromTo("#logo-word", { xPercent: -105 }, { xPercent: 0, duration: 0.75, ease: "power3.out" }, 0.5);""" \
        if split else """
      tl.fromTo("#logo-mark-spin", { scale: 0.3, rotation: -12 },
        { scale: 1, rotation: 0, duration: 0.7, ease: "back.out(2.4)" }, 0);"""
    return f"""
      // ---- logo ----{intro}
      // on every former banner change: the mark pops and spins once
      {json.dumps([t for t in beats if t >= 1.6])}.forEach((t) => {{
        tl.fromTo("#logo-mark-spin", {{ scale: 0.8, rotation: -360 }},
          {{ scale: 1, rotation: 0, duration: 0.8, ease: "back.out(1.8)", immediateRender: false }}, t);
      }});
      // idle: the whole logo floats, the mark sways a little
      tl.fromTo("#logo-float", {{ y: -6 }}, {{ y: 6, duration: 1.2, ease: "sine.inOut",
        repeat: {n_float}, yoyo: true }}, 0);
      tl.fromTo("#logo-mark", {{ rotation: -4 }}, {{ rotation: 4, duration: 1.6, ease: "sine.inOut",
        repeat: {n_wobble}, yoyo: true }}, 1.0);
      // a highlight sweeps across the logo's shape every few seconds
      tl.set("#logo-shine", {{ xPercent: -120 }}, 0);
      for (let t = 1.6; t < {duration:.2f} - 1; t += 4) {{
        tl.fromTo("#logo-shine", {{ xPercent: -120 }},
          {{ xPercent: 420, duration: 1.0, ease: "power2.inOut", immediateRender: false }}, t);
      }}"""


def build_html(m: dict, gsap_src: str) -> str:
    W, H = m["canvas"]["width"], m["canvas"]["height"]
    D = m["source"]["duration"]
    fps = m["source"]["fps"]
    layers = m["layers"]
    ov, caps, audio = layers["overlay"], layers["captions"], layers["audio"]

    css_caption = ""
    if caps:
        css_caption, _, _ = _caption_css(caps["style"], W, H)

    clips = [f"""      <!-- layer 0: footage with overlay/captions painted out -->
      <video id="plate" class="clip" src="{layers["plate"]["src"]}" muted playsinline
        data-start="0" data-duration="{D}" data-track-index="0"></video>"""]
    logo = layers.get("logo")
    css_logo = ""
    if logo:
        css_logo, markup = _logo_markup(logo, W)
        clips.append(markup)
    elif ov and not ov.get("hidden"):
        z = ov["zone_px"]
        clips.append(f"""      <!-- layer 1: animated sponsor overlay, VP9 with alpha ({len(ov["states"])} states, see elements.json) -->
      <video id="overlay" class="clip" src="{ov["src"]}" muted playsinline
        data-start="0" data-duration="{D}" data-track-index="1"
        style="top: {z[1]}px; height: {z[3] - z[1]}px"></video>""")
    if caps:
        clips.append("      <!-- layer 2: word captions (editable text) -->")
        for w in caps["words"]:
            dur = round(max(1 / fps, w["end"] - w["start"]), 3)
            pop = f' data-pop="{",".join(str(x) for x in w["pop"])}"' if w.get("pop") else ""
            clips.append(
                f'      <div id="{w["id"]}" class="clip caption-word" data-start="{w["start"]}" data-duration="{dur}" '
                f'data-track-index="2"{pop}><span class="caption-text">{html.escape(w["text"])}</span></div>')
    if audio:
        clips.append(f"""      <!-- layer 3: {audio.get("label", "original soundtrack")} -->
      <audio id="audio" src="{audio["src"]}" data-start="0" data-duration="{D}" data-track-index="3" data-volume="1"></audio>""")

    pop = (caps or {}).get("style", {}).get("pop_in", {}).get("scales") or [1]
    exit_ = (caps or {}).get("style", {}).get("exit", {}).get("scales") or []
    script = f"""
      // Pop-in measured from the source: glyph scale per frame at {fps:g} fps.
      // Each word carries its own measured curve in data-pop; POP is the typical one.
      const POP = {json.dumps(pop)};
      const EXIT = {json.dumps(exit_)};
      const FRAME = 1 / {fps:g};
      const tl = gsap.timeline({{ paused: true }});
      document.querySelectorAll("#root .caption-word").forEach((clip) => {{
        const word = clip.querySelector(".caption-text");
        const start = parseFloat(clip.dataset.start);
        const end = start + parseFloat(clip.dataset.duration);
        const pop = clip.dataset.pop ? clip.dataset.pop.split(",").map(Number) : POP;
        if (!pop.length) return;
        tl.set(word, {{ scale: pop[0] }}, start);
        pop.slice(1).forEach((s, i) => tl.to(word, {{ scale: s, duration: FRAME, ease: "none" }}, start + i * FRAME));
        if (pop[pop.length - 1] !== 1) tl.to(word, {{ scale: 1, duration: FRAME, ease: "none" }}, start + (pop.length - 1) * FRAME);
        EXIT.forEach((s, i) => tl.to(word, {{ scale: s, duration: FRAME, ease: "none" }}, end - (EXIT.length - i) * FRAME));
      }});{_logo_script(logo, D)}
      window.__timelines["main"] = tl;"""

    body = "\n".join(clips)
    return f"""<!doctype html>
<html lang="{m.get("lang", "ru")}">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={W}, height={H}" />
    <title>vidextract — {html.escape(Path(m["source"]["path"]).stem)}</title>
    <script src="{gsap_src}"></script>
    <style>
      @font-face {{
        font-family: "{FONT_FAMILY}";
        src: url("assets/fonts/{FONT_FAMILY}.ttf") format("truetype");
        font-weight: 300 900;
        font-display: block;
      }}
      body {{
        margin: 0;
        background: #000;
      }}
      #root {{
        position: relative;
        width: 100%;
        height: 100%;
        overflow: hidden;
        background: #000;
      }}
      #plate {{
        position: absolute;
        inset: 0;
        width: 100%;
        height: 100%;
        object-fit: cover;
      }}
      #overlay {{
        position: absolute;
        left: 0;
        width: 100%;
        z-index: 5;
      }}{css_logo}{css_caption}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{D}"
      data-width="{W}" data-height="{H}">
{body}
    </div>
    <script>{script}
    </script>
  </body>
</html>
"""


def write_project(out: Path, manifest: dict) -> None:
    name = out.resolve().name
    fonts = out / "assets" / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / "assets" / "fonts" / f"{FONT_FAMILY}.ttf", fonts / f"{FONT_FAMILY}.ttf")
    shutil.copy(HERE / "assets" / "fonts" / "OFL.txt", fonts / "OFL.txt")
    gsap_src = _vendor_gsap(out / "assets" / "vendor")
    (out / "index.html").write_text(build_html(manifest, gsap_src))
    (out / "meta.json").write_text(json.dumps({"id": name, "name": name}, indent=2))
    (out / "hyperframes.json").write_text(json.dumps({
        "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
        "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
        "media": {"autoProxy": True},
    }, indent=2))
    pin = f"hyperframes@{HYPERFRAMES_VERSION}"
    (out / "package.json").write_text(json.dumps({
        "name": name, "private": True, "type": "module",
        "scripts": {
            "dev": f"npx --yes {pin} preview",
            "check": f"npx --yes {pin} check",
            "render": f"npx --yes {pin} render",
        },
    }, indent=2))
