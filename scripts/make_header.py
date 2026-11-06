"""Write docs/brand/header-dark.svg and header-light.svg: the README header
(the web app's halftone-globe mark, the wordmark and the tagline, framed by
the HUD corner brackets). Text uses system fonts, since GitHub serves SVGs
as images that can't load web fonts.

    python scripts/make_header.py
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_BASE = ROOT.parent / "n1xYosintweb" / "webapp" / "templates" / "base.html"
OUT = ROOT / "docs" / "brand"

# The mark is read from the web app (the same picture in both places).

THEMES = {
    "dark": {"bg": "#0d1117", "ink": "#f2f2f2", "dim": "#8c8c8c", "muted": "#6e7681", "line": "#30363d"},
    "light": {"bg": "#ffffff", "ink": "#111111", "dim": "#6b6b6b", "muted": "#6e7781", "line": "#d0d7de"},
}

MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


def mark_dots() -> str:
    html = WEB_BASE.read_text(encoding="utf-8")
    svg = re.search(r'<svg class="mark"[^>]*>(.*?)</svg>', html, re.S).group(1)
    return re.search(r'<g fill="currentColor">(.*?)</g>', svg, re.S).group(1)


def header(theme: dict, dots: str) -> str:
    w, h = 960, 240
    bracket = 18
    corners = "".join(
        f'<path d="{d}" fill="none" stroke="{theme["dim"]}" stroke-width="1.5"/>'
        for d in (f"M24 {24 + bracket} V24 H{24 + bracket}",
                  f"M{w - 24 - bracket} 24 H{w - 24} V{24 + bracket}",
                  f"M24 {h - 24 - bracket} V{h - 24} H{24 + bracket}",
                  f"M{w - 24 - bracket} {h - 24} H{w - 24} V{h - 24 - bracket}")
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="n1xYosint">
  <rect width="{w}" height="{h}" rx="6" fill="{theme["bg"]}"/>
  {corners}
  <g transform="translate(92 40) scale(6.6667)" color="{theme["ink"]}">
    <circle cx="12" cy="12" r="11.2" fill="none" stroke="currentColor" stroke-width="0.35" opacity=".5"/>
    <g fill="currentColor">{dots}</g>
  </g>
  <text x="300" y="128" font-family="{MONO}" font-size="68" letter-spacing="-1"><tspan font-weight="800" fill="{theme["ink"]}">n1xY</tspan><tspan font-weight="400" fill="{theme["dim"]}">osint</tspan></text>
  <text x="304" y="168" font-family="{SANS}" font-size="19" fill="{theme["dim"]}">Username, email, phone, name and domain reconnaissance</text>
  <text x="304" y="200" font-family="{MONO}" font-size="13" letter-spacing="2" fill="{theme["muted"]}">PUBLIC SOURCES ONLY  ·  CONFIDENCE-SCORED  ·  250+ SOURCES</text>
</svg>
'''


def main() -> None:
    dots = mark_dots()
    OUT.mkdir(parents=True, exist_ok=True)
    for name, theme in THEMES.items():
        (OUT / f"header-{name}.svg").write_text(header(theme, dots), encoding="utf-8", newline="\n")
        print("wrote", OUT / f"header-{name}.svg")


if __name__ == "__main__":
    main()
