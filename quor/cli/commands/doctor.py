"""quor doctor — health check: dependencies, hook, tracking DB, filters, mode, tee."""

from __future__ import annotations

import contextlib
import importlib
import io
import re
import sys
from pathlib import Path

import platformdirs
import typer
from rich.console import Console
from rich.markup import escape

from quor.adapters.hook_manifest import HOOK_SPECS, ClaudeHookSpec
from quor.config.loader import load_user_config
from quor.config.model import QuorUserConfig
from quor.errors import ExitCode
from quor.filters.registry import FilterRegistry

console = Console()

_REQUIRED_PACKAGES = ("typer", "pydantic", "orjson", "platformdirs", "regex", "rich")

_HOOK_SCHEMA_RE = re.compile(r"^# quor-hook-schema: (?P<schema_version>.+)$", re.MULTILINE)


class _FakeStdout:
    """Stand-in for sys.stdout whose .buffer is writable (Python 3.14 makes the real one read-only)."""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, s: str) -> int:
        return 0

    def flush(self) -> None:
        pass


def doctor(
    settings_path: Path | None = typer.Option(
        None, "--settings-path", hidden=True, help="Override the Claude settings.json path (for testing)."
    ),
    reset_tee: bool = typer.Option(
        False,
        "--reset-tee",
        help="Clear tee's adaptive-disable state and re-enable it after fixing a filesystem issue.",
    ),
    fix: bool = typer.Option(
        False,
        "--fix",
        help=(
            "Automatically repair deterministic, safe issues (missing/stale hook "
            "scripts, their settings.json registration) before re-checking."
        ),
    ),
) -> None:
    """Run health checks and print a summary with colored status indicators."""
    _run_doctor(settings_path=settings_path, reset_tee=reset_tee, fix=fix)


def _run_doctor(
    *, settings_path: Path | None = None, reset_tee: bool = False, fix: bool = False
) -> None:
    """The actual health-check logic, callable as plain Python.

    Separated from the Typer-decorated `doctor()` above so callers that need
    to invoke this directly (`quor init --claude` runs doctor automatically
    after installing hooks) get real Python defaults instead of `doctor()`'s
    unresolved `typer.Option(...)` sentinels — calling a Typer command
    function directly bypasses Typer's own CLI-parsing layer, so an unfilled
    parameter receives the raw `OptionInfo` object (truthy) rather than its
    resolved default. `init.py` previously called `doctor()` this way, which
    made `reset_tee` evaluate as True unconditionally and print "Tee
    adaptive-disable state cleared." even when `--reset-tee` was never
    passed.
    """
    if reset_tee:
        from quor.pipeline.tee import reset_tee_state

        try:
            reset_tee_state()
            console.print("[green]Tee adaptive-disable state cleared.[/green]")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Could not reset tee state: {exc}[/red]")

    if fix:
        console.print("[bold]Checking Quor...[/bold]\n")
        fix_results = _repair_hooks(settings_path)
        if fix_results:
            for name, ok, detail in fix_results:
                _print_check_line(name, ok, detail)
        else:
            console.print("[green]✓ Hooks already current[/green]")
        console.print("\n[bold]Re-running checks...[/bold]\n")

    checks: list[tuple[str, bool, str]] = []

    checks.append(_check_python_version())
    checks.extend(_check_dependencies())
    for spec in HOOK_SPECS:
        checks.append(_check_hook_script(spec))
        checks.append(_check_hook_registered(spec, settings_path))
        checks.append(_check_hook_up_to_date(spec))
        roundtrip_result = _run_roundtrip_check(spec.hook_id)
        if roundtrip_result is not None:
            checks.append(roundtrip_result)
    checks.append(_check_hook_collision(settings_path))
    checks.append(_check_sqlite())
    checks.append(_check_filters())
    # Loaded once and shared: _check_mode()/_check_tee() each need
    # QuorUserConfig, and both always run in the same doctor invocation, so
    # loading config.toml separately for each was a guaranteed duplicate
    # read+parse+validate every single `quor doctor` run.
    user_config = load_user_config()
    checks.append(_check_mode(user_config))
    checks.append(_check_tee(user_config))
    checks.append(_check_plugins())

    all_ok = True
    for name, ok, detail in checks:
        _print_check_line(name, ok, detail)
        all_ok = all_ok and ok

    if fix:
        if all_ok:
            console.print("\n[green]✓ All checks passed[/green]")
        else:
            remaining = sum(1 for _, ok, _ in checks if not ok)
            noun = "action" if remaining == 1 else "actions"
            console.print(f"\nDoctor completed with {remaining} manual {noun} remaining.")

    if not all_ok:
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)


def _print_check_line(name: str, ok: bool, detail: str) -> None:
    symbol = "[green]✓[/green]" if ok else "[red]✗[/red]"
    # escape(): `name`/`detail` are dynamic text that can contain literal
    # square brackets (a path segment, or "quor[javascript]" in an extras
    # hint) — Rich's markup parser otherwise reads "[javascript]" as an
    # (unrecognized, silently dropped) style tag, not literal text. Only
    # `symbol`'s own hardcoded `[green]...[/green]` is meant to be parsed
    # as markup here.
    suffix = f" — {escape(detail)}" if detail else ""
    # soft_wrap: detail strings can embed long filesystem paths pushing
    # an actionable snippet like `quor init --claude` past the console
    # width — Rich's default word-wrap would otherwise split it mid-
    # phrase across two lines, which is both harder to read and breaks
    # a clean copy-paste of the suggested command.
    console.print(f"{symbol} {escape(name)}{suffix}", soft_wrap=True)


def _check_python_version() -> tuple[str, bool, str]:
    ok = sys.version_info >= (3, 11)
    return ("Python ≥ 3.11", ok, f"{sys.version_info.major}.{sys.version_info.minor}")


def _check_dependencies() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    for pkg in _REQUIRED_PACKAGES:
        try:
            importlib.import_module(pkg)
            results.append((f"Dependency '{pkg}'", True, ""))
        except ImportError as exc:
            results.append((f"Dependency '{pkg}'", False, str(exc)))
    return results


def _hook_script_path(spec: ClaudeHookSpec) -> Path:
    return Path(platformdirs.user_data_dir("quor")) / "hooks" / spec.script_name


def _check_hook_script(spec: ClaudeHookSpec) -> tuple[str, bool, str]:
    """Does `spec`'s script file exist on disk? Generic across HOOK_SPECS —
    adding a hook to the manifest gets this check for free."""
    hook_path = _hook_script_path(spec)
    exists = hook_path.exists()
    detail = str(hook_path) if exists else f"not found at {hook_path} — run `quor init --claude`"
    return (f"{spec.label} hook script installed", exists, detail)


def _check_hook_registered(spec: ClaudeHookSpec, settings_path: Path | None) -> tuple[str, bool, str]:
    """Does Claude Code's settings.json actually reference `spec`'s script?

    Distinct from `_check_hook_script` above: a script can exist on disk
    (e.g. left over from a prior install) without settings.json actually
    pointing Claude Code at it — that combination previously passed
    "Hook script installed" while Quor was not wired in at all. This check
    closes that gap generically for every hook in HOOK_SPECS.
    """
    from quor.cli.commands.init import _hook_installed, _read_settings
    from quor.errors import ConfigError

    settings_file = settings_path or (Path.home() / ".claude" / "settings.json")
    label = f"{spec.label} hook registered in settings.json"
    if not settings_file.exists():
        return (label, False, f"{settings_file} not found — run `quor init --claude`")
    try:
        settings = _read_settings(settings_file)
    except ConfigError as exc:
        return (label, False, str(exc))
    if _hook_installed(settings, spec):
        return (label, True, "")
    return (
        label,
        False,
        f"no {spec.event} entry in {settings_file} references {spec.script_name} — "
        "run `quor init --claude`",
    )


def _check_hook_up_to_date(spec: ClaudeHookSpec) -> tuple[str, bool, str]:
    """Does the installed script match `spec`'s current hook schema?

    Each generated script embeds a `# quor-hook-schema: N` line (see
    `quor.adapters.hook_manifest.render_hook_script`). Comparing that against
    `spec.schema_version` — not `quor.__version__` — detects a hook whose
    actual definition (template body, registration shape) is stale.
    Deliberately decoupled from the package version: a Quor release that
    doesn't touch this hook's template must not tell every user to reinstall
    it, so `schema_version` only changes when `spec.template` (or how it's
    installed) actually does. "The hook exists and is registered" is not the
    same claim as "the hook matches its current definition," which is what
    this check (not `_check_hook_script`/`_check_hook_registered` above)
    answers. A script with no schema line at all (installed before this
    check existed) is treated as outdated rather than erroring.
    """
    hook_path = _hook_script_path(spec)
    label = f"{spec.label} hook up to date"
    if not hook_path.exists():
        # Nothing installed yet — _check_hook_script already reports this;
        # don't double-report it as also "outdated".
        return (label, True, "(nothing installed yet)")
    content = hook_path.read_text(encoding="utf-8")
    match = _HOOK_SCHEMA_RE.search(content)
    installed_schema = match.group("schema_version").strip() if match else None
    current_schema = str(spec.schema_version)
    if installed_schema == current_schema:
        return (label, True, "")
    from_schema = installed_schema or "an older release (no schema marker)"
    return (
        label,
        False,
        f"installed hook schema is {from_schema}, current schema is {current_schema} — "
        "run `quor init --claude` to refresh",
    )


def _repair_hooks(settings_path: Path | None) -> list[tuple[str, bool, str]]:
    """`quor doctor --fix`'s repair step: deterministically fix any hook in
    HOOK_SPECS that's missing its script, missing its settings.json
    registration, or stale — reusing exactly the same write primitives
    `quor init --claude` uses (`render_hook_script`, `_write_text_atomic`,
    `_install_hook_entry`, `_write_json_atomic`). Not a second implementation
    of that install logic — just invoked directly, without init's
    interactive dry-run/confirmation/conflict-warning wrapper, since a
    repair on an install that already exists needs none of that.

    Deliberately does NOT perform a hook's *first-ever* install: if a hook
    has neither a script nor a settings.json entry, it was simply never set
    up (`quor init --claude` hasn't been run for it yet), which is a
    first-time opt-in decision, not something a repair tool should do on the
    user's behalf — `--fix` repairs an existing install, it does not perform
    one (see module/task framing: "this is NOT an installer"). That case is
    left for the normal check list below to report, with its existing
    "run `quor init --claude`" detail text.

    Returns one result per hook actually repaired, plus one more if
    settings.json was written — empty if every hook is either already
    current or never installed (so `--fix` performs zero writes on an
    already-healthy, or never-initialized, install). Each hook's repair is
    independent and fail-open: one hook's write failing (e.g. a permissions
    error) is reported and does not prevent repairing another hook or
    writing settings.json for the hooks that did succeed.
    """
    from quor.adapters.hook_manifest import render_hook_script
    from quor.cli.commands.init import (
        _install_hook_entry,
        _read_settings,
        _write_json_atomic,
        _write_text_atomic,
    )

    settings_file = settings_path or (Path.home() / ".claude" / "settings.json")
    results: list[tuple[str, bool, str]] = []

    try:
        settings = _read_settings(settings_file)
    except Exception as exc:  # noqa: BLE001 — unreadable settings.json: nothing safe to repair
        return [("Claude settings repaired", False, f"could not read {settings_file}: {exc}")]

    settings_dirty = False
    for spec in HOOK_SPECS:
        script_ok = _check_hook_script(spec)[1]
        registered_ok = _check_hook_registered(spec, settings_path)[1]
        up_to_date_ok = _check_hook_up_to_date(spec)[1]

        if script_ok and registered_ok and up_to_date_ok:
            continue  # already current — no write for this hook

        if not script_ok and not registered_ok:
            continue  # never installed — a repair tool doesn't opt users in

        try:
            script_path = _hook_script_path(spec)
            _write_text_atomic(script_path, render_hook_script(spec, python=sys.executable))
            settings = _install_hook_entry(settings, spec, script_path)
            settings_dirty = True
            results.append((f"{spec.label} hook script regenerated", True, str(script_path)))
        except Exception as exc:  # noqa: BLE001 — one hook's failure must not stop the rest
            results.append((f"{spec.label} hook script regenerated", False, str(exc)))

    if settings_dirty:
        try:
            _write_json_atomic(settings_file, settings)
            results.append(("Claude settings repaired", True, str(settings_file)))
        except Exception as exc:  # noqa: BLE001
            results.append(("Claude settings repaired", False, str(exc)))

    return results


def has_stale_hooks() -> bool:
    """Cheap, read-only check reused by `cli/main.py`'s root callback to nudge
    users toward `quor doctor`/`quor init --claude` after a `pip install
    --upgrade quor` — pip never touches the hook scripts `quor init --claude`
    writes (they live under `platformdirs.user_data_dir`, outside the
    installed package), so a `schema_version` bump in a new release leaves an
    old install silently stale until the user re-runs init.

    Delegates entirely to `_check_hook_up_to_date`, so this can never
    disagree with what `quor doctor` reports for the same files: `ok` there
    is True both when a hook is current *and* when nothing is installed yet
    (`hook_path.exists()` is False), so "not ok" here means specifically
    "installed but outdated" — never "not installed at all".
    """
    return any(not _check_hook_up_to_date(spec)[1] for spec in HOOK_SPECS)


_STALE_WARN_STATE_FILENAME = "stale_hook_warning_state.json"


def _stale_warn_state_path() -> Path:
    return Path(platformdirs.user_data_dir("quor")) / _STALE_WARN_STATE_FILENAME


def _current_schema_signature() -> dict[str, int]:
    """{hook_id: schema_version} for every hook in HOOK_SPECS — the "schema
    world" a stale-hook warning was last shown for. Comparing this (not a
    timestamp) against what's on disk is what makes `should_warn_stale_hooks`
    warn again only when a future release actually bumps a schema_version,
    never on a timer."""
    return {spec.hook_id: spec.schema_version for spec in HOOK_SPECS}


def should_warn_stale_hooks() -> bool:
    """Warn-once-per-schema decision behind `cli/main.py`'s post-upgrade
    nudge (see `has_stale_hooks` docstring for why the nudge exists at all).

    Persists the schema signature (`_current_schema_signature`) last warned
    about to a tiny JSON file under `platformdirs.user_data_dir("quor")`, so
    a stale install that hasn't changed doesn't re-nag on every CLI
    invocation. Purely schema-keyed — no timestamp, no "once per day" — so
    the only things that change the outcome are `quor init --claude` (clears
    the state, since hooks are current again) or a future release bumping a
    `schema_version` (the signature differs from what's stored, so it warns
    again exactly once).

    Fail-open: any error reading or writing the state file is swallowed and
    treated as "no prior warning recorded" — worst case the warning fires
    again next time, which is safe. This must never raise, since it runs
    ahead of every CLI command.
    """
    from quor.cli.commands.init import _read_settings, _write_json_atomic

    state_path = _stale_warn_state_path()

    if not has_stale_hooks():
        # Healthy again (typically right after `quor init --claude`) —
        # leftover state would otherwise cause a *future* real staleness to
        # be silently skipped if it happened to match an old signature.
        with contextlib.suppress(OSError):
            state_path.unlink(missing_ok=True)
        return False

    current = _current_schema_signature()
    try:
        stored = _read_settings(state_path)
    except Exception:  # noqa: BLE001 — corrupt/unreadable state must not block
        stored = {}

    if stored == current:
        return False

    with contextlib.suppress(Exception):  # best-effort persistence only
        _write_json_atomic(state_path, current)
    return True


def _check_hook_collision(settings_path: Path | None = None) -> tuple[str, bool, str]:
    """Warn if another tool's PreToolUse Bash hook is registered alongside Quor's."""
    from quor.adapters.hook_manifest import BASH_HOOK_SPEC
    from quor.cli.commands.init import _find_conflicting_hooks, _read_settings
    from quor.errors import ConfigError

    settings_file = settings_path or (Path.home() / ".claude" / "settings.json")
    if not settings_file.exists():
        return ("No conflicting PreToolUse hooks", True, "")
    try:
        settings = _read_settings(settings_file)
        conflicts = _find_conflicting_hooks(settings, bash_script_name=BASH_HOOK_SPEC.script_name)
        if conflicts:
            detail = (
                f"{len(conflicts)} other Bash hook(s) detected — only one PreToolUse Bash hook "
                "tool can safely be active at a time (Claude Code can silently drop one hook's "
                "rewrite when two are registered). This means disable the other tool, not "
                "leave both running — run `quor init --claude` to see which one."
            )
            return ("No conflicting PreToolUse hooks", False, detail)
        return ("No conflicting PreToolUse hooks", True, "")
    except ConfigError as exc:
        return ("No conflicting PreToolUse hooks", True, f"(could not check: {exc})")
    except Exception:  # noqa: BLE001 — settings file may not exist or be parseable
        return ("No conflicting PreToolUse hooks", True, "")


def _check_hook_roundtrip() -> tuple[str, bool, str]:
    import orjson

    from quor.adapters.claude import run_hook
    from quor.rewrite.invocation import get_quor_invocation

    payload = orjson.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}})
    old_stdin, old_stdout = sys.stdin, sys.stdout
    try:
        sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
        fake_stdout = _FakeStdout()
        sys.stdout = fake_stdout
        run_hook()
        result = orjson.loads(fake_stdout.buffer.getvalue())
        rewritten = result.get("hookSpecificOutput", {}).get("updatedInput", {}).get("command", "")
        expected = f"{get_quor_invocation()} git status"
        if rewritten == expected:
            return ("Hook responds correctly", True, "")
        return ("Hook responds correctly", False, f"unexpected rewrite: {rewritten!r}")
    except Exception as exc:  # noqa: BLE001
        return ("Hook responds correctly", False, str(exc))
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout


def _check_read_hook_roundtrip() -> tuple[str, bool, str]:
    """Capability check for the PostToolUse/Read hook (QB-007C).

    Proves the live wiring actually compresses, not just that the plumbing
    responds: a synthetic PostToolUse/Read payload for a Markdown document
    large enough to exceed the `markdown` filter's token budget must come
    back with `updatedToolOutput` present and strictly smaller than the
    original. This does NOT prove the installed Claude Code binary actually
    honors `updatedToolOutput` for Read (that requires a real Claude Code
    session; see backlog.md's QB-007 entry) — it only proves Quor's own side
    of the contract is well-formed and that compression is genuinely wired
    in, not merely shape-correct.
    """
    import orjson

    from quor.adapters.claude_read import run_hook

    # Large enough to exceed markdown.toml's 2000-token max_tokens budget,
    # so this check exercises the real compression path, not just the
    # "no filter matched" passthrough shape.
    large_doc = "# Title\n\n" + ("Filler prose to exceed the token budget. " * 400)
    payload = orjson.dumps(
        {
            "tool_name": "Read",
            "tool_input": {"file_path": "example.md"},
            "tool_response": large_doc,
        }
    )
    old_stdin, old_stdout = sys.stdin, sys.stdout
    try:
        sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
        fake_stdout = _FakeStdout()
        sys.stdout = fake_stdout
        run_hook()
        result = orjson.loads(fake_stdout.buffer.getvalue())
        hook_specific = result.get("hookSpecificOutput", {})
        if hook_specific.get("hookEventName") != "PostToolUse":
            return (
                "Read hook responds correctly",
                False,
                f"unexpected hookEventName: {hook_specific.get('hookEventName')!r}",
            )
        updated = hook_specific.get("updatedToolOutput")
        if not isinstance(updated, str):
            return (
                "Read hook responds correctly",
                False,
                "expected updatedToolOutput for an oversized Markdown document, got none",
            )
        if len(updated) >= len(large_doc):
            return (
                "Read hook responds correctly",
                False,
                "updatedToolOutput was not smaller than the original document",
            )
        return ("Read hook responds correctly", True, "")
    except Exception as exc:  # noqa: BLE001
        return ("Read hook responds correctly", False, str(exc))
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout


def _run_roundtrip_check(hook_id: str) -> tuple[str, bool, str] | None:
    """Dispatch to `hook_id`'s behavioral (roundtrip) check, if one exists.

    Genuinely hook-specific — proving a hook actually compresses requires a
    hook-specific synthetic payload — so this isn't part of HOOK_SPECS
    itself (also avoids a circular import between hook_manifest.py and this
    module). The "installed / registered / up to date" checks above are the
    parts that generalize, and those are driven by HOOK_SPECS directly in
    `_run_doctor`'s loop; adding a future hook without a branch here still
    gets those three generic checks, just no behavioral verification.

    Deliberately a plain if/elif calling the module-level functions by name,
    not a dict built once at import time (`{"bash": _check_hook_roundtrip,
    ...}`) — a dict literal captures each function object at *module import*
    time, which silently stops seeing `unittest.mock.patch(
    "quor.cli.commands.doctor._check_read_hook_roundtrip", ...)` in tests,
    since patching the module attribute later doesn't reach back into an
    already-built dict. A bare name reference inside a function body is
    resolved fresh on every call, so it does see the patch.
    """
    if hook_id == "bash":
        return _check_hook_roundtrip()
    if hook_id == "read":
        return _check_read_hook_roundtrip()
    return None


def _check_sqlite() -> tuple[str, bool, str]:
    from quor.tracking.db import get_tracking_db

    try:
        db = get_tracking_db()
        db.flush()
        db.close()
        return ("Tracking DB readable/writable", True, "")
    except Exception as exc:  # noqa: BLE001
        return ("Tracking DB readable/writable", False, str(exc))


def _check_filters() -> tuple[str, bool, str]:
    registry = FilterRegistry(project_root=Path.cwd())
    total_failures = 0
    total_skipped = 0
    for _, filter_config in registry.all_filters():
        result = registry.run_tests(filter_config)
        total_failures += len(result.failures)
        total_skipped += len(result.skipped)
    if total_failures:
        return ("Built-in filter tests pass", False, f"{total_failures} inline test failure(s)")
    detail = (
        f"{total_skipped} test(s) skipped — optional AST dependency not installed "
        "(quor[javascript])"
        if total_skipped
        else ""
    )
    return ("Built-in filter tests pass", True, detail)


def _check_mode(user_config: QuorUserConfig) -> tuple[str, bool, str]:
    return (f"Mode: {user_config.mode}", True, "")


def _check_tee(user_config: QuorUserConfig) -> tuple[str, bool, str]:
    """Report tee's status (ADR-023 / QB-013 adaptive fallback).

    Three distinct states, only one of which is flagged as a problem:
      - enabled — normal, healthy state.
      - deliberately disabled by the user (QuorUserConfig.tee_enabled /
        QUOR_TEE_ENABLED) — intentional, not a problem.
      - adaptively disabled after repeated filesystem write failures — a
        real problem worth surfacing (✗), since it means recovery footers
        have silently stopped being written.
    """
    from quor.pipeline.tee import get_tee_status

    try:
        status = get_tee_status()
    except Exception as exc:  # noqa: BLE001
        return ("Tee", True, f"(could not check: {exc})")

    if status.disabled:
        hint = (
            f"auto-disabled after {status.consecutive_failures} consecutive write "
            f"failures ({status.disabled_reason}) — fix the underlying filesystem "
            "issue, then run `quor doctor --reset-tee` to re-enable"
        )
        return ("Tee: disabled (filesystem unavailable)", False, hint)

    if not user_config.tee_enabled:
        return ("Tee: disabled (disabled in config)", True, "")

    return ("Tee: enabled", True, "")


def _check_plugins() -> tuple[str, bool, str]:
    """Report discovered third-party stages and plugins; flag any load failures."""
    from quor.pipeline.plugin_loader import get_load_report

    try:
        report = get_load_report(use_cache=False)
    except Exception as exc:  # noqa: BLE001
        return ("Plugin discovery", True, f"(could not check: {exc})")

    if report.is_empty:
        return ("Plugin discovery", True, "no third-party plugins installed")

    if report.failures:
        names = ", ".join(f.entry_point_name for f in report.failures)
        return (
            "Plugin discovery",
            False,
            f"{len(report.failures)} load failure(s): {names}",
        )

    parts: list[str] = []
    if report.stages:
        stage_names = ", ".join(s.stage_type for s in report.stages)
        parts.append(f"{len(report.stages)} stage(s): {stage_names}")
    if report.plugins:
        plugin_names = ", ".join(f"{p.plugin_id}@{p.version}" for p in report.plugins)
        parts.append(f"{len(report.plugins)} plugin(s): {plugin_names}")
    return ("Plugin discovery", True, "; ".join(parts))
