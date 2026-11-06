"""Regenerate docs/terminal.svg, the README's terminal demo, from a real run.

It plays once when shown: the command, the banner, the progress bar filling
(the run's real check count and tallies), then the results row by row and
the summary. Only the first rows of the two largest categories are drawn,
labelled as trimmed; the summary is the whole run's. Real requests go out
(one scan).

    python scripts/terminal_demo.py torvalds
"""
from __future__ import annotations

import asyncio
import re
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from rich.console import Console
from rich.text import Text

from osintrecon.core.config import Config
from osintrecon.core.engine import Engine
from osintrecon.core.models import Identifier, IdentifierType, MatchStatus
from osintrecon.output.banner import show_banner
from osintrecon.output.renderer import render, render_summary

ROWS_PER_CATEGORY = 5
CATEGORIES_SHOWN = 2
WIDTH = 118


class Tally:
    """Counts the run's progress events, for the demo's progress line."""

    def __init__(self):
        self.total = self.done = self.found = self.failed = 0

    def __call__(self, event: dict) -> None:
        if event.get("event") == "round":
            self.total += len(event.get("checks") or [])
        elif event.get("event") == "check":
            self.done += 1
            outcome = event.get("outcome")
            if outcome in ("found", "probable"):
                self.found += 1
            elif outcome in ("error", "rate_limited"):
                self.failed += 1


def progress_line(tally: Tally, seconds: float) -> Text:
    counts = f" {tally.done}/{tally.total}  "
    tail = f"{tally.found} found · {tally.failed} couldn't answer  0:{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"
    head = "⠿ Checking sources "
    bar = "━" * max(10, WIDTH - len(head) - len(counts) - len(tail) - 1)
    text = Text()
    text.append("⠿ ", style="grey62")
    text.append("Checking sources ", style="bold")
    text.append(bar, style="bright_white")
    text.append(counts)
    text.append(f"{tally.found} found", style="bold green")
    text.append(f" · {tally.failed} couldn't answer", style="grey62")
    text.append(f"  0:{int(seconds) // 60:02d}:{int(seconds) % 60:02d}", style="yellow")
    return text


def animate(svg: str, progress_line_no: int) -> str:
    """Make the static export play once: every line fades in on its turn,
    the progress line is revealed left to right like a filling bar."""
    line_height = 24.4

    def delay(line: int) -> float:
        if line == 0:
            return 0.2
        if line < progress_line_no:
            return 0.9 + 0.08 * line
        if line == progress_line_no:
            return 1.9
        return 4.8 + 0.06 * (line - progress_line_no)

    def tag_text(match: re.Match) -> str:
        line = int(match.group(2))
        return f'{match.group(1)} style="animation-delay:{delay(line):.2f}s" data-l="{line}"'

    svg = re.sub(r'(<text class="[^"]*"[^>]*clip-path="url\(#[^)]*-line-(\d+)\)")', tag_text, svg)

    def tag_rect(match: re.Match) -> str:
        y = float(match.group(2))
        line = int((y + 2) // line_height)
        return f'{match.group(1)} style="animation-delay:{delay(line):.2f}s"'

    svg = re.sub(r'(<rect fill="[^"]*" x="[0-9.]+" y="([0-9.]+)"[^>]*?)(?=\s*/>)', tag_rect, svg)
    # rich gives every <text> and background <rect> a terminal-NNN class
    # prefix; the matrix group holds them all.
    prefix = re.search(r"\.(terminal-\d+)-matrix", svg).group(1)
    css = (f"\n    .{prefix}-matrix text, .{prefix}-matrix ~ g rect, g[clip-path] rect[shape-rendering] "
           "{ opacity: 0; animation: n1x-show .2s ease-out forwards; }\n"
           "    @keyframes n1x-show { to { opacity: 1; } }\n")
    svg = svg.replace("</style>", css + "    </style>", 1)
    # The progress line's clip rectangle grows from nothing to full width.
    svg = re.sub(
        rf'(<clipPath id="{prefix}-line-{progress_line_no}">\s*<rect x="0" y="[0-9.]+" width=")([0-9.]+)("[^/]*)/>',
        lambda m: (f'{m.group(1)}0{m.group(3)}><animate attributeName="width" from="0" to="{m.group(2)}" '
                   f'begin="1.9s" dur="2.6s" fill="freeze"/></rect>'),
        svg, count=1)
    return svg


def main() -> None:
    username = sys.argv[1] if len(sys.argv) > 1 else "torvalds"
    out_svg = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("docs/terminal.svg")

    tally = Tally()
    result = asyncio.run(Engine(Config.load(None), progress=tally).run(
        [Identifier(value=username, type=IdentifierType.USERNAME)]))

    found = [f for f in result.findings if f.status in (MatchStatus.CONFIRMED, MatchStatus.PROBABLE)]
    by_category: dict[str, list] = defaultdict(list)
    for f in sorted(found, key=lambda x: -x.confidence):
        by_category[f.category].append(f)
    biggest = sorted(by_category, key=lambda c: -len(by_category[c]))[:CATEGORIES_SHOWN]
    shown = [f for c in biggest for f in by_category[c][:ROWS_PER_CATEGORY]]
    errors = [f for f in result.findings if f.status is MatchStatus.ERROR]
    hidden = len(found) - len(shown)

    console = Console(record=True, width=WIDTH, force_terminal=True, color_system="truecolor")
    console.print(f"[grey50]$[/] n1xyosint -u {username}")
    show_banner(console, solid_blocks=True)
    progress_line_no = console.export_text(clear=False).count("\n")
    console.print(progress_line(tally, result.stats.elapsed))
    console.print()
    render(replace(result, findings=shown + errors, entities=[], rejected_inputs=[]), console=console, summary=False)
    if hidden > 0:
        console.print(f"[grey50]  ... {hidden} more findings (trimmed for this screenshot)[/grey50]")
    render_summary(result, console)

    out_svg.parent.mkdir(parents=True, exist_ok=True)
    svg = console.export_svg(title=f"n1xyosint -u {username}")
    out_svg.write_text(animate(svg, progress_line_no), encoding="utf-8", newline="\n")
    print(f"wrote {out_svg} ({len(shown)} of {len(found)} findings shown, progress line {progress_line_no})")


if __name__ == "__main__":
    main()
