"""The start-up banner: "n1xYosint" in block letters, "n1xY" bright and
"osint" dim (the same weighting as the web logo), with the credit line under
it. Shown only on an interactive terminal; piped or redirected output stays
clean, and --no-banner turns it off."""
from __future__ import annotations

from rich.console import Console
from rich.text import Text

from osintrecon import __version__

# ANSI Shadow, generated once with pyfiglet (not a dependency).
N1XY = (
    '███╗   ██╗ ██╗██╗  ██╗██╗   ██╗',
    '████╗  ██║███║╚██╗██╔╝╚██╗ ██╔╝',
    '██╔██╗ ██║╚██║ ╚███╔╝  ╚████╔╝ ',
    '██║╚██╗██║ ██║ ██╔██╗   ╚██╔╝  ',
    '██║ ╚████║ ██║██╔╝ ██╗   ██║   ',
    '╚═╝  ╚═══╝ ╚═╝╚═╝  ╚═╝   ╚═╝   ',
)
OSINT = (
    ' ██████╗ ███████╗██╗███╗   ██╗████████╗',
    '██╔═══██╗██╔════╝██║████╗  ██║╚══██╔══╝',
    '██║   ██║███████╗██║██╔██╗ ██║   ██║   ',
    '██║   ██║╚════██║██║██║╚██╗██║   ██║   ',
    '╚██████╔╝███████║██║██║ ╚████║   ██║   ',
    ' ╚═════╝ ╚══════╝╚═╝╚═╝  ╚═══╝   ╚═╝   ',
)
WIDTH = len(N1XY[0]) + len(OSINT[0])
REPO_URL = "https://github.com/n11xY/n1xYosint"


def _append_art(text: Text, line: str, colour: str, solid: bool) -> None:
    if not solid:
        text.append(line, style=f"bold {colour}")
        return
    # Full blocks drawn as cell backgrounds: an SVG export (the README
    # screenshot) leaves gaps between lines of block glyphs; a background
    # never does.
    for char in line:
        text.append(" " if char == "█" else char, style=f"on {colour}" if char == "█" else colour)


def banner_text(width: int, solid_blocks: bool = False) -> Text:
    text = Text()
    if width >= WIDTH + 2:
        for left, right in zip(N1XY, OSINT):
            _append_art(text, left, "#f2f2f2", solid_blocks)
            _append_art(text, right.rstrip(), "#8c8c8c", solid_blocks)
            text.append("\n")
    else:
        text.append("n1xY", style="bold bright_white")
        text.append("osint", style="grey62")
        text.append("\n")
    text.append("developed by ", style="grey50")
    text.append("n1xY", style="bold")
    text.append(f"  ·  v{__version__}  ·  ", style="grey50")
    text.append(REPO_URL.removeprefix("https://"), style=f"grey50 link {REPO_URL}")
    text.append("\n")
    return text


def show_banner(console: Console, solid_blocks: bool = False) -> None:
    console.print(banner_text(console.width, solid_blocks), highlight=False)
