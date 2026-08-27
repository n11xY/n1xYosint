"""Terminal renderer -- categorized, color-coded presentation of findings
and run statistics using `rich`."""
from __future__ import annotations

from collections import defaultdict

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from osintrecon.core.engine import RunResult
from osintrecon.core.models import Finding, MatchStatus

STATUS_STYLE = {
    MatchStatus.CONFIRMED: "bold green",
    MatchStatus.PROBABLE: "yellow",
    MatchStatus.UNCERTAIN: "dim yellow",
    MatchStatus.ERROR: "red",
    MatchStatus.NOT_FOUND: "dim",
}


def render(result: RunResult, console: Console | None = None, show_uncertain: bool = True) -> None:
    console = console or Console()

    actionable = [
        f for f in result.findings
        if f.status not in (MatchStatus.NOT_FOUND, MatchStatus.ERROR)
        and (show_uncertain or f.status != MatchStatus.UNCERTAIN)
    ]
    errors = [f for f in result.findings if f.status == MatchStatus.ERROR]

    by_category: dict[str, list[Finding]] = defaultdict(list)
    for f in actionable:
        by_category[f.category].append(f)

    if not actionable:
        console.print("[yellow]No findings above the reporting threshold.[/yellow]")
    for category, items in sorted(by_category.items()):
        table = Table(title=f"[bold]{category}[/bold]", show_lines=False, expand=True)
        table.add_column("Identifier")
        table.add_column("Source")
        table.add_column("Status")
        table.add_column("Confidence", justify="right")
        table.add_column("Title / URL")

        for f in sorted(items, key=lambda x: -x.confidence):
            style = STATUS_STYLE.get(f.status, "")
            table.add_row(
                f.identifier.value,
                f.source,
                f"[{style}]{f.status.value}[/{style}]",
                f"{f.confidence:.2f}",
                f"{f.title}\n[link={f.source_url}]{f.source_url}[/link]",
            )
        console.print(table)

    if errors:
        console.print(Panel(
            "\n".join(f"- {e.source}: {e.title} ({e.metadata.get('error', '')})" for e in errors[:20]),
            title=f"[red]Source errors ({len(errors)})[/red]", border_style="red",
        ))

    if result.rejected_inputs:
        console.print(Panel(
            "\n".join(f"- {raw!r}: {reason}" for raw, reason in result.rejected_inputs),
            title="[red]Rejected inputs[/red]", border_style="red",
        ))

    stats_table = Table(title="Execution statistics", show_header=False)
    for key, value in result.stats.to_dict().items():
        stats_table.add_row(key.replace("_", " "), str(value))
    console.print(stats_table)

    if result.entities:
        multi = [e for e in result.entities if len(e.identifiers) > 1]
        if multi:
            console.print(f"[bold cyan]{len(multi)} correlated entit{'y' if len(multi)==1 else 'ies'} "
                           f"(linked across multiple identifiers)[/bold cyan]")
