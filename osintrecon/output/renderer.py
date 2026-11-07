"""Terminal renderer -- categorized, color-coded presentation of findings
and run statistics using `rich`."""
from __future__ import annotations

from collections import defaultdict

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from osintrecon.core.account_evidence import Analysis, analyze as analyze_evidence, node_of
from osintrecon.core.doctor import DoctorReport
from osintrecon.core.engine import RunResult
from osintrecon.core.entity_scoring import confidence_bucket
from osintrecon.core.models import Finding, MatchStatus
from osintrecon.core.source_names import SOURCE_NAMES, source_name  # noqa: F401 -- re-exported

STATUS_STYLE = {
    MatchStatus.CONFIRMED: "bold green",
    MatchStatus.PROBABLE: "yellow",
    MatchStatus.UNCERTAIN: "dim yellow",
    MatchStatus.ERROR: "red",
    MatchStatus.NOT_FOUND: "dim",
}

STATUS_LABEL = {
    MatchStatus.CONFIRMED: "✔ confirmed",
    MatchStatus.PROBABLE: "◐ probable",
    MatchStatus.UNCERTAIN: "? uncertain",
}


# Human names for plugin slugs (the web UI uses the same ones).
def _problem_text(finding: Finding) -> str:
    """A source problem's message without the site name it starts with."""
    name = source_name(finding.source)
    title = finding.title or ""
    return title[len(name) + 2:] if title.startswith(name + ": ") else title


CONFIDENCE_BUCKET_STYLE = {
    "very strong": "bold green",
    "strong": "green",
    "possible": "yellow",
    "weak": "dim yellow",
    "very weak": "dim",
}


def render(result: RunResult, console: Console | None = None, show_uncertain: bool = True,
           summary: bool = True) -> None:
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
    evidence = analyze_evidence(result.findings)

    for category, items in sorted(by_category.items()):
        table = Table(title=f"[bold]{category}[/bold] [grey50]({len(items)})[/grey50]", title_justify="left",
                      box=box.SIMPLE_HEAD, header_style="grey62", show_lines=False, expand=True)
        table.add_column("Status", no_wrap=True)
        table.add_column("Source", no_wrap=True)
        table.add_column("Identifier", no_wrap=True)
        if show_hop:
            table.add_column("Hop", justify="right")
        table.add_column("Finding / URL", ratio=1)
        table.add_column("Conf.", justify="right", no_wrap=True)

        for f in sorted(items, key=lambda x: -x.confidence):
            style = STATUS_STYLE.get(f.status, "")
            row = [
                f"[{style}]{STATUS_LABEL.get(f.status, f.status.value)}[/{style}]",
                f"[bold]{source_name(f.source)}[/bold]" + _tie_mark(evidence, f),
                f.identifier.value,
            ]
            if show_hop:
                row.append(str(f.hop))
            row += [
                f"{f.title}\n[grey62][link={f.source_url}]{f.source_url}[/link][/grey62]",
                f"{f.confidence:.0%}",
            ]
            table.add_row(*row)
        console.print(table)

    if errors:
        # One line per source (a search over several identifiers otherwise
        # repeats the same site once per identifier).
        by_source: dict[str, Finding] = {}
        for e in errors:
            by_source[e.source] = e
        lines = [f"[bold]{source_name(src)}[/bold]  [grey62]{_problem_text(e)}[/grey62]"
                 for src, e in sorted(by_source.items(), key=lambda kv: source_name(kv[0]).lower())]
        shown = lines[:20]
        if len(lines) > 20:
            shown.append(f"[grey50]...and {len(lines) - 20} more[/grey50]")
        console.print(Panel(
            "\n".join(shown),
            title=f"[yellow]Couldn't answer this run ({len(by_source)})[/yellow]", title_align="left",
            border_style="grey35", subtitle="[grey50]not the same as \"not found\"[/grey50]",
            subtitle_align="right",
        ))

    if result.rejected_inputs:
        console.print(Panel(
            "\n".join(f"- {raw!r}: {reason}" for raw, reason in result.rejected_inputs),
            title="[red]Rejected inputs[/red]", border_style="red",
        ))

    render_evidence(result, console, evidence)

    if result.entities:
        multi = [e for e in result.entities if len(e.identifiers) > 1]
        if multi:
            console.print(f"[bold cyan]{len(multi)} correlated entit{'y' if len(multi)==1 else 'ies'} "
                           f"(linked across multiple identifiers)[/bold cyan]")
            for e in sorted(multi, key=lambda x: -x.confidence):
                bucket = confidence_bucket(e.confidence)
                style = CONFIDENCE_BUCKET_STYLE.get(bucket, "")
                identifiers_line = ", ".join(
                    f"{i.type.value}:{i.value}"
                    for i in sorted(e.identifiers, key=lambda i: (i.type.value, i.value))
                )
                sources_line = ", ".join(sorted({f.source for f in e.findings}))

                body = [f"[bold]Identifiers:[/bold] {identifiers_line}"]
                if e.reasons:
                    body.append("[bold]Evidence:[/bold]")
                    body.extend(f"  - {reason}" for reason in e.reasons)
                body.append(f"[bold]Sources:[/bold] {sources_line}")

                console.print(Panel(
                    "\n".join(body),
                    title=f"[{style}]Entity {e.entity_id} -- {bucket} ({e.confidence:.0%})[/{style}]",
                    border_style=style or "cyan",
                ))

    if summary:
        render_summary(result, console, show_uncertain)

def _tie_mark(evidence: Analysis, f: Finding) -> str:
    verdict = evidence.verdicts.get(node_of(f))
    if verdict is None or not verdict.tied:
        return ""
    return f" [cyan]⇄{verdict.cluster + 1}[/cyan]"


_STRENGTH_STYLE = {"strong": "green", "medium": "yellow", "weak": "grey62"}


def render_evidence(result: RunResult, console: Console | None = None, evidence: Analysis | None = None) -> None:
    """Which found accounts are tied together and by what (⇄N in the
    tables marks group N), weaker hints, contradictions, and how telling
    each username is."""
    console = console or Console()
    evidence = evidence or analyze_evidence(result.findings)
    # A name found nowhere has no matches to weigh.
    rarity = [r for r in getattr(result, "username_rarity", None) or [] if r.get("hits") or not r.get("checked")]
    if not (evidence.links or evidence.conflicts or rarity):
        return
    lines: list[str] = []
    for r in rarity:
        style = {"rare": "green", "moderate": "yellow", "common": "red"}.get(r["label"], "")
        share = f", found on {r['hits']} of {r['checked']} sites" if r.get("checked") else ""
        meaning = {"rare": "the same name elsewhere is more likely the same person",
                   "moderate": "weigh matches on the evidence below",
                   "common": "the same name elsewhere is often someone else"}.get(r["label"], "")
        lines.append(f"[bold]{r['username']}[/bold]: [{style}]{r['label']}[/{style}] username "
                     f"[grey50](~{round(r['bits'])} bits{share})[/grey50] -- {meaning}")
    for i, members in enumerate(evidence.clusters, 1):
        names = ", ".join(evidence.labels.get(n, n[0]) for n in members)
        lines.append(f"[cyan]⇄{i}[/cyan] [bold]{names}[/bold]")
        for link in evidence.cluster_links(i - 1):
            style = _STRENGTH_STYLE[link.strength]
            lines.append(f"     [{style}]{link.strength:6}[/{style}] {link.detail}")
    if not evidence.clusters:
        lines.append("[grey62]No found accounts are tied together by evidence: the shared name is all they "
                     "have in common.[/grey62]")
    hints = evidence.loose_links()
    if hints:
        lines.append("[bold]Weaker hints[/bold] [grey50](not enough on their own)[/grey50]")
        for link in hints[:8]:
            lines.append(f"     [{_STRENGTH_STYLE[link.strength]}]{link.strength:6}[/] {link.detail}")
        if len(hints) > 8:
            lines.append(f"     [grey50]...and {len(hints) - 8} more[/grey50]")
    if evidence.conflicts:
        lines.append("[bold red]Contradictions[/bold red] [grey50](likely different people behind the same name)[/grey50]")
        # Contradictions inside a linked group first: those are the surprising ones.
        tied_nodes = {n for members in evidence.clusters for n in members}
        ordered = sorted(evidence.conflicts, key=lambda c: -((c.a in tied_nodes) + (c.b in tied_nodes)))
        lines.extend(f"     {c.detail}" for c in ordered[:5])
        if len(ordered) > 5:
            lines.append(f"     [grey50]...and {len(ordered) - 5} more[/grey50]")
    console.print(Panel("\n".join(lines), title="[bold cyan]Account evidence[/bold cyan]", title_align="left",
                        border_style="cyan", subtitle="[grey50]what ties the found accounts together[/grey50]",
                        subtitle_align="right"))


def render_summary(result: RunResult, console: Console | None = None, show_uncertain: bool = True) -> None:
    """The closing summary: what was found, what couldn't answer, and what the run cost."""
    console = console or Console()
    actionable = [
        f for f in result.findings
        if f.status not in (MatchStatus.NOT_FOUND, MatchStatus.ERROR)
        and (show_uncertain or f.status != MatchStatus.UNCERTAIN)
    ]
    errors = [f for f in result.findings if f.status == MatchStatus.ERROR]
    counts = {status: sum(1 for f in actionable if f.status is status)
              for status in (MatchStatus.CONFIRMED, MatchStatus.PROBABLE, MatchStatus.UNCERTAIN)}
    stats = result.stats
    summary = (
        f"[bold]{len(actionable)} finding{'s' if len(actionable) != 1 else ''}[/bold]  "
        f"[bold green]{counts[MatchStatus.CONFIRMED]} confirmed[/bold green] · "
        f"[yellow]{counts[MatchStatus.PROBABLE]} probable[/yellow] · "
        f"[dim yellow]{counts[MatchStatus.UNCERTAIN]} uncertain[/dim yellow]"
    )
    if errors:
        summary += f"  [grey62]· {len({e.source for e in errors})} couldn't answer[/grey62]"
    details = (
        f"[grey50]{stats.sources_queried} source modules · {stats.requests_sent} requests "
        f"({stats.cache_hits} from cache) · {stats.duplicates_removed} duplicates merged · "
        f"{stats.elapsed:.1f}s[/grey50]"
    )
    console.print(Panel(f"{summary}\n{details}", border_style="grey35", box=box.ROUNDED))


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
    if report.proxy:
        if report.proxy_reachable:
            proxy_status = f"{report.proxy} [green](reachable)[/green]"
        else:
            proxy_status = f"{report.proxy} [red](not reachable: {report.proxy_error})[/red]"
    else:
        proxy_status = "[dim]none configured[/dim]"
    setup_table.add_row("Proxy", proxy_status)
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

    if report.proxy and not report.proxy_reachable:
        console.print(
            f"[red]Proxy {report.proxy} is not reachable ({report.proxy_error}).[/red] "
            "[dim]Every request will fail until it's actually running -- e.g. for Tor: "
            "sudo apt install tor && sudo systemctl start tor[/dim]"
        )

    ready_count = sum(1 for c in report.sources if c.ready)
    console.print(f"\n[bold]{ready_count}/{len(report.sources)} sources ready[/bold] "
                  f"({report.elapsed:.1f}s)")
