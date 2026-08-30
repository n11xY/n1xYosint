"""CLI entrypoint for n1xYosint.

Usage examples:
    n1xyosint -u johndoe -e john@example.com
    n1xyosint -f targets.txt --export json:out.json --export csv:out.csv
    n1xyosint --interactive
    n1xyosint -u johndoe --config config/config.yaml --save-evidence -v
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from rich.console import Console

from osintrecon.core.config import Config
from osintrecon.core.doctor import run_doctor
from osintrecon.core.engine import Engine
from osintrecon.core.logging_setup import setup_logging
from osintrecon.core.normalize import load_from_file, normalize_batch
from osintrecon.output import exporters
from osintrecon.output.renderer import render, render_doctor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="n1xyosint",
        description="Modular OSINT reconnaissance framework for username/email intelligence "
                    "gathering, limited to publicly accessible sources and legitimate APIs.",
    )
    parser.add_argument("-u", "--username", action="append", default=[], help="Username to investigate (repeatable)")
    parser.add_argument("-e", "--email", action="append", default=[], help="Email to investigate (repeatable)")
    parser.add_argument("-f", "--file", help="Path to a file with one identifier per line")
    parser.add_argument("--interactive", action="store_true", help="Prompt for identifiers interactively")
    parser.add_argument("-c", "--config", help="Path to a YAML/JSON config file")
    parser.add_argument("--doctor", action="store_true",
                         help="Run setup diagnostics (config, filesystem paths, DNS reachability "
                              "per source) and exit -- no identifiers needed")
    parser.add_argument("--export", action="append", default=[], metavar="FORMAT:PATH",
                         help="Export results, e.g. --export json:out.json (repeatable; formats: json,csv,txt)")
    parser.add_argument("--save-evidence", action="store_true", help="Persist raw responses as evidence files")
    parser.add_argument("--proxy", help="Proxy URL, e.g. socks5h://127.0.0.1:9050 or http://127.0.0.1:8080")
    parser.add_argument("--concurrency", type=int, help="Max overall concurrent requests")
    parser.add_argument("--timeout", type=float, help="Per-request timeout in seconds")
    parser.add_argument("--no-cache", action="store_true", help="Disable the response cache for this run")
    parser.add_argument("--hide-uncertain", action="store_true", help="Hide UNCERTAIN-confidence findings in the terminal view")
    parser.add_argument("--depth", type=int, default=1, metavar="N",
                         help="Enrichment depth: also investigate identifiers discovered along the way "
                              "(e.g. an email found in a bio), this many rounds deep. Default 1 = seeds only.")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase log verbosity (-v, -vv)")
    parser.add_argument("--log-file", help="Write full logs to this file")
    return parser


def _collect_raw_identifiers(args: argparse.Namespace) -> list[str]:
    raw: list[str] = list(args.username) + list(args.email)
    if args.file:
        raw.extend(load_from_file(args.file))
    if args.interactive:
        console = Console()
        console.print("[bold]Interactive mode[/bold] - enter one username or email per line, blank line to finish:")
        while True:
            try:
                line = input("> ").strip()
            except EOFError:
                break
            if not line:
                break
            raw.append(line)
    return raw


def _apply_cli_overrides(config: Config, args: argparse.Namespace) -> None:
    if args.proxy:
        config.data["proxy"] = args.proxy
    if args.concurrency:
        config.data["concurrency"] = args.concurrency
    if args.timeout:
        config.data["timeout_seconds"] = args.timeout
    if args.no_cache:
        config.data.setdefault("cache", {})["enabled"] = False
    if args.save_evidence:
        config.data.setdefault("evidence", {})["save_raw"] = True


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    level = "DEBUG" if args.verbose >= 2 else ("INFO" if args.verbose == 1 else "WARNING")
    log = setup_logging(level=level, log_file=args.log_file)

    if args.doctor:
        try:
            config = Config.load(args.config)
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            Console().print(f"[red]Config error: {exc}[/red]")
            return 1
        _apply_cli_overrides(config, args)
        report = asyncio.run(run_doctor(config))
        render_doctor(report, console=Console())
        has_problems = (
            not report.cache_writable
            or (report.evidence_checked and not report.evidence_writable)
            or any(not d.ok for c in report.sources for d in c.domains)
        )
        return 1 if has_problems else 0

    raw_identifiers = _collect_raw_identifiers(args)
    if not raw_identifiers:
        parser.error("no identifiers supplied: use -u/-e, --file, or --interactive")

    normalization = normalize_batch(raw_identifiers)
    if normalization.rejected:
        for raw, reason in normalization.rejected:
            log.warning("rejected input %r: %s", raw, reason)
    if not normalization.identifiers:
        Console().print("[red]No valid identifiers to investigate after normalization.[/red]")
        return 1

    try:
        config = Config.load(args.config)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        Console().print(f"[red]Config error: {exc}[/red]")
        return 1
    _apply_cli_overrides(config, args)

    engine = Engine(config)
    result = asyncio.run(engine.run(normalization.identifiers, depth=args.depth))
    result.rejected_inputs = normalization.rejected

    console = Console()
    render(result, console=console, show_uncertain=not args.hide_uncertain)

    for spec in args.export:
        if ":" not in spec:
            console.print(f"[red]Invalid --export spec (want FORMAT:PATH): {spec}[/red]")
            continue
        fmt, path = spec.split(":", 1)
        try:
            exporters.export(result, path, fmt)
            console.print(f"[green]Exported {fmt.upper()} -> {path}[/green]")
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
