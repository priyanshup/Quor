"""quor benchmark <target_path> — offline compression benchmark (QB-129).

Runs the exact same rule-based filter pipeline `compress_context` (MCP) and
`quor <cmd>` (CLI dispatch) both go through — `apply_filter_pipeline()`,
QB-114's shared entry point — directly, in-process, against real files on
disk. No subprocess, no MCP transport, no network call, and nothing here
ever could call an LLM API: Quor's compression is deterministic and
rule-based end to end (regex/AST/structured-data stages against
`quor/filters/*.toml`), the same as every other Quor command — there is no
code path anywhere in this codebase that reaches an LLM. "Strictly offline"
is not a special mode this command opts into; it's the only mode that
exists.

Deliberately uses `apply_filter_pipeline()` (dispatcher.py), not
`quor.mcp.server.compress_context()` — both run the identical compression
logic, but `compress_context()` also calls `track_invocation_safe()`
(QB-105), which would write a real row into the user's `quor.db` for every
benchmarked file. `quor gain`'s numbers are meant to answer "how much has
Quor actually saved me," and a benchmark run isn't real usage — it must
never silently inflate or skew that history. `apply_filter_pipeline()`
still runs the real tee step (ADR-023), so a benchmarked file's compressed
output is tee-recoverable exactly like a real compression would be; that's
an accepted, self-cleaning side effect (tee's own 7-day/500MB retention),
not a telemetry-accuracy problem the way an extra `quor.db` row would be.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import orjson
import typer
from rich.console import Console
from rich.table import Table

from quor.cli.format_utils import format_count, format_percentage
from quor.config.loader import (
    find_and_load_project_config,
    load_user_config,
    resolve_effective_config,
)
from quor.engine.dispatcher import apply_filter_pipeline
from quor.errors import ExitCode
from quor.tracking.db import count_tokens

console = Console(highlight=False)


@dataclass(frozen=True)
class BenchmarkResult:
    """One benchmarked file's measurements. `filter_name` is `None` for a
    passthrough (no filter matched `match_content_types`/`match_command`
    for this content) — same convention `InvocationRecord.filter_name`
    already uses."""

    path: Path
    raw_tokens: int
    compressed_tokens: int
    latency_ms: float
    filter_name: str | None

    @property
    def savings_fraction(self) -> float:
        if self.raw_tokens == 0:
            return 0.0
        return max(0.0, 1 - self.compressed_tokens / self.raw_tokens)


def benchmark(
    target_path: Path = typer.Argument(
        ..., help="File or directory to benchmark. A directory is walked recursively."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Also show which filter matched (or 'passthrough') per file."
    ),
    output_format: str = typer.Option(
        "text", "--format", help="Output format: 'text' (default, a Rich table) or 'json'."
    ),
) -> None:
    """Benchmark Quor's compression pipeline against real files: raw vs.
    compressed token counts, net savings %, and per-file latency. Entirely
    offline — see this module's docstring for why that's not a mode toggle."""
    if output_format not in ("text", "json"):
        typer.secho(
            f"✗ --format must be 'text' or 'json', got {output_format!r}", fg=typer.colors.RED
        )
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)

    if not target_path.exists():
        typer.secho(f"✗ {target_path}: no such file or directory", fg=typer.colors.RED)
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)

    files = _collect_files(target_path)
    results: list[BenchmarkResult] = []
    skipped: list[tuple[Path, str]] = []
    for path in files:
        result_or_reason = _benchmark_file(path)
        if isinstance(result_or_reason, BenchmarkResult):
            results.append(result_or_reason)
        else:
            skipped.append((path, result_or_reason))

    if not results:
        typer.secho(f"✗ {target_path}: no readable text files found", fg=typer.colors.RED)
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)

    if output_format == "json":
        _print_json(results, skipped)
        return
    _print_table(results, skipped, verbose=verbose)


def _collect_files(target_path: Path) -> list[Path]:
    """A single file benchmarks itself; a directory is walked recursively
    for every regular file (directories/symlinks excluded), sorted for
    deterministic output order across runs."""
    if target_path.is_file():
        return [target_path]
    return sorted(p for p in target_path.rglob("*") if p.is_file())


def _benchmark_file(path: Path) -> BenchmarkResult | str:
    """Returns a `BenchmarkResult`, or a short skip reason (e.g. "not valid
    UTF-8 text") for a file that can't be benchmarked as text — a binary
    file in the walked directory must not abort the whole run."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "not valid UTF-8 text (binary file)"
    except OSError as exc:
        return f"could not read file: {exc}"

    raw_tokens = count_tokens(raw_text)
    if raw_tokens == 0:
        return "empty file"

    # QB-130: resolved relative to this file's own path — walks up looking
    # for a .quor.toml the same way the MCP server's focal_file path does.
    # Fail-open, same reasoning as quor/mcp/server.py's
    # _resolve_project_overrides(): a project-config resolution error must
    # never take down an otherwise-successful benchmark run.
    try:
        project_config = find_and_load_project_config(path)
        overrides = resolve_effective_config(load_user_config(), project_config)
    except Exception:  # noqa: BLE001 — fail-open: config resolution must never break a benchmark run
        overrides = load_user_config()

    t0 = time.monotonic()
    compressed, filter_config = apply_filter_pipeline(
        raw_text,
        raw_text,
        file_path=path,
        min_token_threshold=overrides.min_token_threshold,
        exclude_patterns=overrides.exclude_patterns,
    )
    latency_ms = (time.monotonic() - t0) * 1000

    return BenchmarkResult(
        path=path,
        raw_tokens=raw_tokens,
        compressed_tokens=count_tokens(compressed),
        latency_ms=latency_ms,
        filter_name=filter_config.name if filter_config is not None else None,
    )


def _print_table(
    results: list[BenchmarkResult], skipped: list[tuple[Path, str]], *, verbose: bool
) -> None:
    console.print("[bold]Quor Benchmark[/bold]")
    console.print()

    table = Table(show_header=True, header_style="bold")
    table.add_column("File")
    table.add_column("Raw Tokens", justify="right")
    table.add_column("Compressed", justify="right")
    table.add_column("Savings", justify="right")
    table.add_column("Latency (ms)", justify="right")
    if verbose:
        table.add_column("Filter")

    for r in results:
        row = [
            str(r.path),
            format_count(r.raw_tokens),
            format_count(r.compressed_tokens),
            f"[green]{format_percentage(r.savings_fraction)}[/green]",
            f"{r.latency_ms:.2f}",
        ]
        if verbose:
            row.append(r.filter_name or "[dim]passthrough[/dim]")
        table.add_row(*row)

    console.print(table)
    console.print()

    total_raw = sum(r.raw_tokens for r in results)
    total_compressed = sum(r.compressed_tokens for r in results)
    total_latency = sum(r.latency_ms for r in results)
    total_savings = max(0.0, 1 - total_compressed / total_raw) if total_raw else 0.0

    console.print(
        f"[bold]{len(results)} file(s)[/bold]   "
        f"~{format_count(total_raw)} -> ~{format_count(total_compressed)} tokens   "
        f"([bold green]{format_percentage(total_savings)} saved[/bold green])   "
        f"{total_latency:.2f} ms total"
    )

    if skipped:
        console.print()
        console.print(f"[dim]{len(skipped)} file(s) skipped:[/dim]")
        for path, reason in skipped:
            console.print(f"[dim]  {path}: {reason}[/dim]")

    console.print()
    console.print(
        "[dim]· Token counts are estimated via the char/4 approximation, "
        "±20% versus a real tokenizer. Runs entirely offline — no network "
        "or LLM API call.[/dim]"
    )


def _print_json(results: list[BenchmarkResult], skipped: list[tuple[Path, str]]) -> None:
    total_raw = sum(r.raw_tokens for r in results)
    total_compressed = sum(r.compressed_tokens for r in results)
    payload = {
        "files": [
            {
                "path": str(r.path),
                "raw_tokens": r.raw_tokens,
                "compressed_tokens": r.compressed_tokens,
                "savings_pct": round(r.savings_fraction * 100, 2),
                "latency_ms": round(r.latency_ms, 4),
                "filter": r.filter_name,
            }
            for r in results
        ],
        "skipped": [{"path": str(path), "reason": reason} for path, reason in skipped],
        "total_raw_tokens": total_raw,
        "total_compressed_tokens": total_compressed,
        "total_savings_pct": round((max(0.0, 1 - total_compressed / total_raw) * 100), 2)
        if total_raw
        else 0.0,
        "total_latency_ms": round(sum(r.latency_ms for r in results), 4),
    }
    typer.echo(orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode())
