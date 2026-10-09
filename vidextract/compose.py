"""Write the extracted layers as a HyperFrames project (index.html + assets)."""

from __future__ import annotations

import html
import json
import math
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


TICKER_GREEN = "#2f7a3a"


def _ticker_items(logo: dict, url_first: bool) -> str:
    """One loop of ticker content: brand name and site separated by the mark
    (the wordmark stands in when no brand text was given). Repeated pictures are
    CSS backgrounds so the renderer sees each image file once."""
    sep = '<span class="tk-sep"></span>' if logo.get("mark") else '<span class="tk-dot"></span>'
    brand, url = logo.get("brand"), logo.get("url")
    if brand or url:
        words = [w for w in (brand, url) if w]
        if url_first:
            words.reverse()
        cells = [f'<span class="tk-text">{html.escape(w)}</span>' for w in words]
    else:
        cells = ['<span class="tk-word"></span>']
    one = "".join(c + sep for c in cells)
    return one * 3  # wider than the band so one half always covers it


def _ticker_markup(logo: dict, W: int) -> tuple[str, str]:
    """A tilted green band whose faces sit on a 3D box: a running ticker,
    the logo (the wordmark when the mark already sits in the badge), the ticker
    again, the logo again. The box turns to the next
    face on the original banner's rhythm, the way the sponsor banner did."""
    z = logo["zone_px"]
    bw, bh = round(W * 0.86), round(W * 0.14)
    cx, cy = W // 2, (z[1] + z[3]) // 2
    badge = round(bh * 1.45)
    mark = logo.get("mark")
    word = logo.get("word")
    word_src = word["src"] if word else logo["src"]
    word_aspect = (logo.get("aspect") or 3) * (word["width"] if word else 1)
    half = [_ticker_items(logo, url_first=False), _ticker_items(logo, url_first=True)]
    faces = []
    for i in range(4):
        if i % 2 == 0:
            loop = half[i // 2]
            body = f'<div class="tk-track" id="tk-track-{i}"><div class="tk-half">{loop}</div><div class="tk-half">{loop}</div></div>'
        else:
            body = '<span class="tk-logo"></span>'
        faces.append(f'          <div class="tk-face tk-face-{"run" if i % 2 == 0 else "logo"}" '
                     f'style="transform: rotateY({i * 90}deg) translateZ({bw / 2:.1f}px)">{body}</div>')
    badge_html = (f'\n        <div id="tk-badge"><img src="{mark["src"]}" alt="" /></div>' if mark else "")
    css = f"""
      #tk-tilt {{
        position: absolute;
        left: {cx - bw // 2}px;
        top: {cy - bh // 2}px;
        width: {bw}px;
        height: {bh}px;
        z-index: 5;
        transform: rotate(-3deg);
      }}
      #tk-stage {{
        position: absolute;
        inset: 0;
        perspective: {bw * 2.6:.0f}px;
      }}
      #tk-cube {{
        position: absolute;
        inset: 0;
        transform-style: preserve-3d;
      }}
      .tk-face {{
        position: absolute;
        inset: 0;
        overflow: hidden;
        backface-visibility: hidden;
        display: flex;
        align-items: center;
        box-shadow: 0 {bh * 0.1:.0f}px {bh * 0.22:.0f}px rgba(0, 0, 0, 0.45);
      }}
      .tk-face-run {{
        background: linear-gradient(180deg, #3b8a3f 0%, {TICKER_GREEN} 55%, #276b31 100%);
      }}
      .tk-face-logo {{
        background: #fff;
        justify-content: center;
      }}
      .tk-logo {{
        height: {"94%" if mark else "78%"};
        aspect-ratio: {(word_aspect if mark else logo.get("aspect") or 3):.4f};
        margin-left: {badge * 0.5 if mark else 0:.0f}px;
        background: url("{word_src if mark else logo["src"]}") center / contain no-repeat;
      }}
      .tk-track,
      .tk-half {{
        display: flex;
        align-items: center;
        flex: none;
        white-space: nowrap;
      }}
      .tk-text {{
        font-family: "{FONT_FAMILY}", sans-serif;
        font-weight: 800;
        font-size: {bh * 0.46:.0f}px;
        line-height: 1;
        letter-spacing: 0.02em;
        text-transform: uppercase;
        color: #fff;
        padding: 0 {bh * 0.22:.0f}px;
      }}
      .tk-sep {{
        flex: none;
        width: {bh * 0.5:.0f}px;
        height: {bh * 0.5:.0f}px;
        border-radius: 50%;
        background: #fff {f'url("{mark["src"]}") center / contain no-repeat' if mark else ""};
        box-shadow: 0 0 0 {bh * 0.03:.0f}px #fff;
      }}
      .tk-dot {{
        width: {bh * 0.14:.0f}px;
        height: {bh * 0.14:.0f}px;
        border-radius: 50%;
        background: #bfe3bf;
        margin: 0 {bh * 0.2:.0f}px;
      }}
      .tk-word {{
        flex: none;
        height: {bh * 0.56:.0f}px;
        aspect-ratio: {word_aspect:.4f};
        margin: 0 {bh * 0.22:.0f}px;
        background: url("{word_src}") center / contain no-repeat;
        filter: brightness(0) invert(1);
      }}
      #tk-badge {{
        position: absolute;
        left: {-badge * 0.12:.0f}px;
        top: 50%;
        width: {badge}px;
        height: {badge}px;
        margin-top: {-badge / 2:.0f}px;
        border-radius: 50%;
        background: #fff;
        border: {bh * 0.07:.0f}px solid #fff;
        box-shadow: 0 {bh * 0.06:.0f}px {bh * 0.16:.0f}px rgba(0, 0, 0, 0.45);
      }}
      #tk-badge img {{
        display: block;
        width: 100%;
        height: 100%;
        object-fit: contain;
      }}"""
    markup = f"""      <!-- layer 1: brand ticker (replaces the extracted overlay): a tilted green band with a running
           brand/site ticker; it turns like a box to the next face where the original banner changed -->
      <div id="tk-tilt">
        <div id="tk-stage">
          <div id="tk-cube">
{chr(10).join(faces)}
          </div>
        </div>{badge_html}
      </div>"""
    return css, markup


def _ticker_script(logo: dict, duration: float, W: int) -> str:
    beats = [t for t in (logo.get("beats") or []) if t >= 1.6]
    turn = 0.9
    speed = 0.17 * W  # px/s; the FunPay icon ticker ran ~180 px/s at 1080 wide
    return f"""
      // ---- brand ticker ----
      const TURNS = {json.dumps(beats)};  // the original banner changed here; the box lands on each
      const TURN = {turn};
      const half = document.querySelector("#tk-cube").offsetWidth / 2;
      tl.set("#tk-cube", {{ z: -half, rotationY: 0 }}, 0);
      tl.set("#tk-tilt", {{ scale: 1 }}, 0);
      // intro: the box swings in from the side
      tl.fromTo("#tk-cube", {{ z: -half, rotationY: 90 }},
        {{ z: -half, rotationY: 0, duration: 0.8, ease: "back.out(1.4)", immediateRender: false }}, 0);
      tl.fromTo("#tk-tilt", {{ scale: 0.4 }}, {{ scale: 1, duration: 0.7, ease: "back.out(1.6)", immediateRender: false }}, 0);
      TURNS.forEach((t, n) => {{
        const t0 = Math.max(0.9, t - TURN);
        tl.fromTo("#tk-cube", {{ z: -half, rotationY: -90 * n }},
          {{ z: -half, rotationY: -90 * (n + 1), duration: TURN, ease: "power2.inOut", immediateRender: false }}, t0);
        tl.fromTo("#tk-tilt", {{ scale: 1 }}, {{ scale: 0.8, duration: TURN / 2, ease: "power2.in", immediateRender: false }}, t0);
        tl.fromTo("#tk-tilt", {{ scale: 0.8 }}, {{ scale: 1, duration: TURN / 2, ease: "back.out(2)", immediateRender: false }}, t0 + TURN / 2);
      }});
      // the tickers run right-to-left the whole time, at the original icon ticker's pace
      // (each track holds two identical halves, so one half's width is a seamless loop)
      ["#tk-track-0", "#tk-track-2"].forEach((sel) => {{
        const loop = document.querySelector(sel).scrollWidth / 2 / {speed:.1f};
        tl.fromTo(sel, {{ xPercent: 0 }}, {{ xPercent: -50, duration: loop, ease: "none",
          repeat: Math.ceil({duration:.2f} / loop) }}, 0);
      }});
      if (document.querySelector("#tk-badge")) {{
        tl.fromTo("#tk-badge", {{ scale: 0, rotation: -200 }},
          {{ scale: 1, rotation: 0, duration: 0.8, ease: "back.out(1.7)" }}, 0.2);
        tl.set("#tk-badge", {{ rotationY: 0 }}, 0);
        TURNS.forEach((t) => {{
          tl.fromTo("#tk-badge", {{ rotationY: 0 }},
            {{ rotationY: 360, duration: TURN + 0.2, ease: "power2.inOut", immediateRender: false }}, Math.max(0.9, t - TURN));
        }});
      }}"""


def _panels_markup(header: dict, W: int) -> tuple[str, str]:
    """Header panels on the faces of a turning prism (4 panels = a box), the way the
    original sponsor banner turned from one card to the next."""
    z = header["zone_px"]
    panels = header["panels"]
    n = len(panels)
    aspect = header.get("aspect", 2.6)
    bw = round(W * 0.92)
    bh = round(bw / aspect)
    cx, cy = W // 2, (z[1] + z[3]) // 2
    depth = bw / 2 / math.tan(math.pi / n) if n > 2 else bh * 0.05
    faces = "\n".join(
        f'          <div class="hd-face" style="transform: rotateY({i * 360 / n:.3f}deg) translateZ({depth:.1f}px); '
        f'background-image: url(&quot;{src}&quot;)"></div>' for i, src in enumerate(panels))
    css = f"""
      #hd-wrap {{
        position: absolute;
        left: {cx - bw // 2}px;
        top: {cy - bh // 2}px;
        width: {bw}px;
        height: {bh}px;
        z-index: 5;
        perspective: {bw * 2.6:.0f}px;
      }}
      #hd-prism {{
        position: absolute;
        inset: 0;
        transform-style: preserve-3d;
      }}
      .hd-face {{
        position: absolute;
        inset: 0;
        backface-visibility: hidden;
        background: #fff center / cover no-repeat;
        border-top: {max(2, bh * 0.012):.0f}px solid #c9a227;
        border-bottom: {max(2, bh * 0.012):.0f}px solid #c9a227;
        box-shadow: 0 {bh * 0.05:.0f}px {bh * 0.12:.0f}px rgba(0, 0, 0, 0.45);
      }}"""
    markup = f"""      <!-- layer 1: header panels ({n}) on a turning prism; it turns to the next panel on the
           original banner rhythm (or at a steady pace when the source had no banner) -->
      <div id="hd-wrap">
        <div id="hd-prism">
{faces}
        </div>
      </div>"""
    return css, markup


def _notify_text(text: str) -> str:
    """Escape, and set the “quoted” word in the accent colour, like the brand's taglines."""
    import re
    out = html.escape(text)
    return re.sub(r"(“[^”]+”|&quot;[^&]+&quot;)", r'<span class="nt-accent">\1</span>', out)


def _notify_markup(header: dict, W: int) -> tuple[str, str]:
    """A phone-notification card at the top: the brand icon, the brand name, and one
    tagline at a time. Each tagline drops in, stays, and slides back up."""
    z = header["zone_px"]
    cw = round(W * 0.9)
    fs = round(W * 0.05)
    icon = header.get("icon")
    cards = []
    for i, text in enumerate(header["texts"]):
        icon_html = f'<span class="nt-icon" style="background-image: url(&quot;{icon}&quot;)"></span>' if icon else ""
        cards.append(
            f'        <div class="nt-card" id="nt-{i}">{icon_html}<span class="nt-body">'
            f'<span class="nt-head"><span class="nt-app">{html.escape(header.get("app", ""))}</span>'
            f'<span class="nt-now">now</span></span><span class="nt-text">{_notify_text(text)}</span></span></div>')
    css = f"""
      #nt-wrap {{
        position: absolute;
        left: {(W - cw) // 2}px;
        top: {z[1] + round(W * 0.02)}px;
        width: {cw}px;
        z-index: 5;
      }}
      .nt-card {{
        position: absolute;
        left: 0;
        top: 0;
        width: 100%;
        box-sizing: border-box;
        display: flex;
        align-items: center;
        gap: {round(W * 0.03)}px;
        padding: {round(W * 0.03)}px {round(W * 0.035)}px;
        border-radius: {round(W * 0.05)}px;
        background: rgba(246, 246, 244, 0.95);
        box-shadow: 0 {round(W * 0.02)}px {round(W * 0.05)}px rgba(0, 0, 0, 0.4);
        font-family: "{FONT_FAMILY}", sans-serif;
      }}
      .nt-icon {{
        flex: none;
        width: {round(W * 0.13)}px;
        height: {round(W * 0.13)}px;
        border-radius: 50%;
        background: #fff center / contain no-repeat;
      }}
      .nt-body {{
        display: flex;
        flex-direction: column;
        flex: 1;
        min-width: 0;
        gap: {round(W * 0.006)}px;
      }}
      .nt-head {{
        display: flex;
        justify-content: space-between;
        font-size: {round(fs * 0.7)}px;
        line-height: 1.1;
      }}
      .nt-app {{
        font-weight: 600;
        color: #1b1b1b;
      }}
      .nt-now {{
        color: #808080;
      }}
      .nt-text {{
        font-weight: 800;
        font-size: {fs}px;
        line-height: 1.08;
        color: {header.get("color", "#1f6b3a")};
      }}
      .nt-accent {{
        color: {header.get("accent", "#c08a1e")};
      }}"""
    markup = f"""      <!-- layer 1: header as a phone notification: one tagline at a time drops in from the top -->
      <div id="nt-wrap">
{chr(10).join(cards)}
      </div>"""
    return css, markup


def _notify_script(header: dict, duration: float) -> str:
    hold, gap = header.get("hold", 4.5), header.get("gap", 0.8)
    return f"""
      // ---- header notifications: drop in, stay, slide back up; the next tagline follows ----
      const NT_N = {len(header["texts"])};
      const NT_HOLD = {hold}, NT_GAP = {gap}, NT_IN = 0.55, NT_OUT = 0.45;
      for (let k = 0; k < NT_N; k++) tl.set("#nt-" + k, {{ yPercent: -160, opacity: 1 }}, 0);
      for (let t = 0.5, k = 0; t < {duration:.2f} - 1; t += NT_IN + NT_HOLD + NT_OUT + NT_GAP, k++) {{
        const card = "#nt-" + (k % NT_N);
        tl.fromTo(card, {{ yPercent: -160 }}, {{ yPercent: 0, duration: NT_IN, ease: "back.out(1.3)", immediateRender: false }}, t);
        tl.fromTo(card, {{ yPercent: 0 }}, {{ yPercent: -160, duration: NT_OUT, ease: "power2.in", immediateRender: false }},
          t + NT_IN + NT_HOLD);
      }}"""


def _panels_script(header: dict) -> str:
    n = len(header["panels"])
    beats = [t for t in (header.get("beats") or []) if t >= 1.6]
    return f"""
      // ---- header panels ----
      const HD_TURNS = {json.dumps(beats)};
      const HD_STEP = {360 / n:.4f};
      const HD_DEPTH = {0 if n <= 2 else 1} * (document.querySelector("#hd-prism").offsetWidth / 2 / Math.tan(Math.PI / {n}));
      tl.set("#hd-prism", {{ z: -HD_DEPTH, rotationY: 0 }}, 0);
      tl.set("#hd-wrap", {{ scale: 1 }}, 0);
      tl.fromTo("#hd-prism", {{ z: -HD_DEPTH, rotationY: 90 }},
        {{ z: -HD_DEPTH, rotationY: 0, duration: 0.8, ease: "back.out(1.3)", immediateRender: false }}, 0);
      tl.fromTo("#hd-wrap", {{ scale: 0.4 }}, {{ scale: 1, duration: 0.7, ease: "back.out(1.6)", immediateRender: false }}, 0);
      HD_TURNS.forEach((t, k) => {{
        const t0 = Math.max(0.9, t - 0.9);
        tl.fromTo("#hd-prism", {{ z: -HD_DEPTH, rotationY: -HD_STEP * k }},
          {{ z: -HD_DEPTH, rotationY: -HD_STEP * (k + 1), duration: 0.9, ease: "power2.inOut", immediateRender: false }}, t0);
        tl.fromTo("#hd-wrap", {{ scale: 1 }}, {{ scale: 0.82, duration: 0.45, ease: "power2.in", immediateRender: false }}, t0);
        tl.fromTo("#hd-wrap", {{ scale: 0.82 }}, {{ scale: 1, duration: 0.45, ease: "back.out(2)", immediateRender: false }}, t0 + 0.45);
      }});"""


def _footer_rows(logo: dict, W: int, H: int) -> tuple[int, int]:
    aspect = logo.get("aspect") or 3
    fw = round(W * logo.get("footer_width_pct", 62) / 100)
    fh = round(fw / aspect)
    bottom = round(H * logo.get("footer_bottom_pct", 4.5) / 100)
    return H - bottom - fh, H - bottom


def _footer_markup(logo: dict, W: int, H: int) -> tuple[str, str]:
    """The brand logo standing still at the bottom of the frame, below the captions."""
    aspect = logo.get("aspect") or 3
    fw = round(W * logo.get("footer_width_pct", 62) / 100)
    fh = round(fw / aspect)
    bottom = round(H * logo.get("footer_bottom_pct", 4.5) / 100)
    css = f"""
      #footer-logo {{
        position: absolute;
        left: {(W - fw) // 2}px;
        top: {H - bottom - fh}px;
        width: {fw}px;
        height: {fh}px;
        z-index: 5;
        object-fit: contain;
        filter: drop-shadow(0 0 3px rgba(255, 255, 255, 0.6)) drop-shadow(0 0 14px rgba(255, 255, 255, 0.3))
          drop-shadow(0 6px 12px rgba(0, 0, 0, 0.5));
      }}"""
    markup = f"""      <!-- footer: brand logo, static -->
      <img id="footer-logo" src="{logo["src"]}" alt="" />"""
    return css, markup


def _box_caption_css(style: dict, W: int) -> str:
    return f"""
      .box-cap {{
        position: absolute;
        transform: translate(-50%, -50%);
        box-sizing: border-box;
        max-width: {round(W * 0.94)}px;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: {style["font_px"] * 0.12:.0f}px {style["font_px"] * 0.4:.0f}px;
        background: {style["box_color"]};
        color: {style["text_color"]};
        font-family: "{FONT_FAMILY}", sans-serif;
        font-weight: 500;
        font-size: {style["font_px"]:.0f}px;
        line-height: 1.12;
        text-align: center;
        z-index: 4;
      }}
      .box-cap.card {{
        font-weight: 600;
        line-height: 1.02;
        text-transform: uppercase;
      }}
      .note-cap {{
        position: absolute;
        box-sizing: border-box;
        border-radius: 8px;
        backdrop-filter: blur(14px);
        background: rgba(0, 0, 0, 0.18);
        z-index: 4;
      }}
      .note-cap .note-text {{
        position: absolute;
        font-family: "{FONT_FAMILY}", sans-serif;
        font-weight: 500;
        line-height: 1.25;
        color: #fff;
        text-shadow: 0 1px 3px rgba(0, 0, 0, 0.6);
      }}"""


def _box_caption_clips(caps: dict, fps: float) -> list[str]:
    """One clip per phrase: a box of the source colour over the original box, holding
    the (translated) text. Boxes that moved in the source follow their track."""
    out = ["      <!-- layer 2: phrase captions in boxes (each covers the original box; editable text) -->"]
    for p in caps["phrases"]:
        if "text_out" in p and not p["text_out"].strip():
            continue   # translated to nothing (an icon OCR mistook for words): leave the original alone
        text = p.get("text_out") or p["text"]
        if p.get("uppercase"):
            text = text.upper()
        _, cx, cy, w, h = p["track"][0]
        fs = ""
        if p.get("card"):
            # title cards: the text fills the card like the original's big lettering
            n_lines = max(1, len(text.split()))
            fs = f" font-size: {min(h / n_lines * 0.62, w / max(4, max(len(t) for t in text.split())) * 1.45):.0f}px;"
        dur = round(max(1 / fps, p["end"] - p["start"]), 3)
        out.append(
            f'      <div id="{p["id"]}" class="clip box-cap{" card" if p.get("card") else ""}" data-start="{p["start"]}" '
            f'data-duration="{dur}" data-track-index="2" style="left: {cx}px; top: {cy}px; min-width: {w}px; '
            f'min-height: {h}px;{fs}"><span>{html.escape(text)}</span></div>')
    return out


def _note_clips(notes: list[dict], fps: float, keep_clear: tuple[int, int] | None = None) -> list[str]:
    """Small print near the bottom (a disclaimer): the original is blurred out and the
    (translated) text is written in its place, same size and alignment — or just above
    `keep_clear` (the footer logo's rows) when its place is under the footer."""
    out = ["      <!-- layer 2b: small on-screen notices, original blurred out -->"] if notes else []
    for n in notes:
        x, y, w, h = n["bbox"]
        lines = max(1, n.get("lines", 1))
        fs = h / lines / 1.12
        pad = round(fs * 0.5)
        dur = round(max(1 / fps, n["end"] - n["start"]), 3)
        text = n.get("text_out") or n["text"]
        text_top = pad - fs * 0.1
        if keep_clear and y < keep_clear[1] and y + h > keep_clear[0]:
            text_top = keep_clear[0] - round(fs * 0.6) - h - (y - pad)   # same layout, lifted above the footer
        out.append(
            f'      <div id="{n["id"]}" class="clip note-cap" data-start="{n["start"]}" data-duration="{dur}" '
            f'data-track-index="2" style="left: {x - pad}px; top: {y - pad}px; width: {w + 2 * pad}px; '
            f'height: {h + 2 * pad}px"><span class="note-text" style="left: {pad}px; top: {text_top:.0f}px; '
            f'width: {round(w * 1.25)}px; font-size: {fs:.0f}px">{html.escape(text)}</span></div>')
    return out


def _box_caption_script(caps: dict) -> str:
    moves = {p["id"]: p["track"] for p in caps["phrases"] if len(p["track"]) > 1}
    if not moves:
        return ""
    return f"""
      // boxes that slid in the source follow the same path, frame by frame
      const BOX_TRACKS = {json.dumps(moves)};
      Object.entries(BOX_TRACKS).forEach(([id, keys]) => {{
        const box = document.getElementById(id);
        if (box) keys.forEach(([t, x, y, w, h]) => tl.set(box, {{ left: x, top: y, minWidth: w, minHeight: h }}, t));
      }});"""


def build_html(m: dict, gsap_src: str) -> str:
    W, H = m["canvas"]["width"], m["canvas"]["height"]
    D = m["source"]["duration"]
    fps = m["source"]["fps"]
    layers = m["layers"]
    ov, caps, audio = layers["overlay"], layers["captions"], layers["audio"]

    css_caption = ""
    boxed = bool(caps) and caps.get("kind") == "box"
    if boxed:
        css_caption = _box_caption_css(caps["style"], W)
    elif caps:
        css_caption, _, _ = _caption_css(caps["style"], W, H)

    clips = [f"""      <!-- layer 0: footage with overlay/captions painted out -->
      <video id="plate" class="clip" src="{layers["plate"]["src"]}" muted playsinline
        data-start="0" data-duration="{D}" data-track-index="0"></video>"""]
    logo = layers.get("logo")
    css_logo = ""
    header = layers.get("header")
    style = (logo or {}).get("style")
    ticker = bool(logo) and style == "ticker"
    footer = bool(logo) and style == "footer"
    notify = bool(header) and header.get("style") == "notify"
    if header:
        css_hd, markup = (_notify_markup if notify else _panels_markup)(header, W)
        css_logo += css_hd
        clips.append(markup)
    if logo:
        css_l, markup = (_footer_markup(logo, W, H) if footer
                         else (_ticker_markup if ticker else _logo_markup)(logo, W))
        css_logo += css_l
        clips.append(markup)
    if not logo and not header and ov and not ov.get("hidden"):
        z = ov["zone_px"]
        clips.append(f"""      <!-- layer 1: animated sponsor overlay, VP9 with alpha ({len(ov["states"])} states, see elements.json) -->
      <video id="overlay" class="clip" src="{ov["src"]}" muted playsinline
        data-start="0" data-duration="{D}" data-track-index="1"
        style="top: {z[1]}px; height: {z[3] - z[1]}px"></video>""")
    if boxed:
        clips += _box_caption_clips(caps, fps)
        clear = _footer_rows(logo, W, H) if footer else None
        clips += _note_clips(caps.get("notes") or [], fps, clear)
    elif caps:
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
      }});{_box_caption_script(caps) if boxed else ""}{(_notify_script(header, D) if notify else _panels_script(header)) if header else ""}{_ticker_script(logo, D, W) if ticker else ("" if footer else _logo_script(logo, D))}
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
