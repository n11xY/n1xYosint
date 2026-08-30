"""Terminal renderer -- categorized, color-coded presentation of findings
and run statistics using `rich`."""
from __future__ import annotations

from collections import defaultdict

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from osintrecon.core.doctor import DoctorReport
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

    show_hop = any(f.hop > 0 for f in actionable)

    for category, items in sorted(by_category.items()):
        table = Table(title=f"[bold]{category}[/bold]", show_lines=False, expand=True)
        table.add_column("Identifier")
        if show_hop:
            table.add_column("Hop", justify="right")
        table.add_column("Source")
        table.add_column("Status")
        table.add_column("Confidence", justify="right")
        table.add_column("Title / URL")

        for f in sorted(items, key=lambda x: -x.confidence):
            style = STATUS_STYLE.get(f.status, "")
            row = [f.identifier.value]
            if show_hop:
                row.append(str(f.hop))
            row += [
                f.source,
                f"[{style}]{f.status.value}[/{style}]",
                f"{f.confidence:.2f}",
                f"{f.title}\n[link={f.source_url}]{f.source_url}[/link]",
            ]
            table.add_row(*row)
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


def render_doctor(report: DoctorReport, console: Console | None = None, verbose: bool = False) -> None:
    """Checklist-style output for `n1xyosint --doctor`."""
    console = console or Console()

    setup_table = Table(title="Setup", show_header=False)
    setup_table.add_row(
        "Cache path writable",
        "[green]yes[/green]" if report.cache_writable else f"[red]no: {report.cache_error}[/red]",
    )
    if report.evidence_checked:
        setup_table.add_row(
            "Evidence path writable",
            "[green]yes[/green]" if report.evidence_writable else f"[red]no: {report.evidence_error}[/red]",
        )
    setup_table.add_row("Proxy", report.proxy or "[dim]none configured[/dim]")
    console.print(setup_table)

    sources_table = Table(title="Sources", expand=True)
    sources_table.add_column("Source")
    sources_table.add_column("Status")
    sources_table.add_column("Domains reachable")

    unreachable_domains: list[tuple[str, str, str]] = []  # (source, domain, error)

    for check in sorted(report.sources, key=lambda c: c.name):
        if not check.ready:
            sources_table.add_row(check.name, f"[dim]skipped -- {check.reason}[/dim]", "")
            continue

        ok_count = sum(1 for d in check.domains if d.ok)
        total = len(check.domains)
        for d in check.domains:
            if not d.ok:
                unreachable_domains.append((check.name, d.domain, d.error))

        if total == 0:
            domain_summary = "[dim]n/a[/dim]"
        elif ok_count == total:
            domain_summary = f"[green]{ok_count}/{total} resolved[/green]"
        elif ok_count == 0:
            domain_summary = f"[red]0/{total} resolved[/red]"
        else:
            domain_summary = f"[yellow]{ok_count}/{total} resolved[/yellow]"

        sources_table.add_row(check.name, "[green]ready[/green]", domain_summary)

    console.print(sources_table)

    if unreachable_domains:
        lines = [f"- {source}: {domain} ({error})" for source, domain, error in unreachable_domains]
        console.print(Panel(
            "\n".join(lines),
            title=f"[red]DNS resolution failed ({len(unreachable_domains)} domain(s))[/red]",
            border_style="red",
        ))
        console.print(
            "[dim]These sources will error out mid-scan. If unrelated domains resolve fine, "
            "this is likely a network/VPN/DNS-filtering issue specific to those hosts, not a "
            "framework bug -- see README for troubleshooting.[/dim]"
        )

    ready_count = sum(1 for c in report.sources if c.ready)
    console.print(f"\n[bold]{ready_count}/{len(report.sources)} sources ready[/bold] "
                  f"({report.elapsed:.1f}s)")
