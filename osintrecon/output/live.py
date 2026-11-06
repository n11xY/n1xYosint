"""Live scan progress for the terminal: one bar for every planned check,
with running counts of what was found and what couldn't answer, fed by the
engine's progress events (core/progress.py). Shown only on an interactive
terminal; it disappears when the scan ends, leaving the results."""
from __future__ import annotations

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

_FOUND = ("found", "probable")


class ScanProgress:
    def __init__(self, console: Console):
        self.console = console
        self.found = 0
        self.uncertain = 0
        self.failed = 0
        self.total = 0
        self._progress = Progress(
            SpinnerColumn(style="grey62"),
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=None, complete_style="bright_white", finished_style="bright_white",
                      style="grey23"),
            MofNCompleteColumn(),
            TextColumn("{task.fields[counts]}"),
            TimeElapsedColumn(),
            console=console, transient=True, expand=True,
        )
        self._task = None

    def __enter__(self) -> "ScanProgress":
        self._progress.start()
        self._task = self._progress.add_task("Checking sources", total=None, counts="")
        return self

    def __exit__(self, *exc) -> None:
        self._progress.stop()

    def _counts(self) -> str:
        parts = [f"[bold green]{self.found} found[/]"]
        if self.uncertain:
            parts.append(f"[yellow]{self.uncertain} uncertain[/]")
        if self.failed:
            parts.append(f"[grey62]{self.failed} couldn't answer[/]")
        return " · ".join(parts)

    def __call__(self, event: dict) -> None:
        kind = event.get("event")
        if kind == "round":
            self.total += len(event.get("checks") or [])
            hop = event.get("hop", 0)
            label = "Checking sources" if not hop else f"Following leads (round {hop + 1})"
            self._progress.update(self._task, total=self.total, description=label)
        elif kind == "check":
            outcome = event.get("outcome")
            if outcome in _FOUND:
                self.found += 1
            elif outcome == "uncertain":
                self.uncertain += 1
            elif outcome in ("error", "rate_limited"):
                self.failed += 1
            self._progress.update(self._task, advance=1, counts=self._counts())
        elif kind == "phase":
            self._progress.update(self._task, description="Correlating findings")
