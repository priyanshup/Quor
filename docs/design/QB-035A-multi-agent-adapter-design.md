# QB-035A — Multi-Agent Adapter Architecture Design

**Status:** Design complete. No runtime code changed. Design-only branch
(`feature/qb-035a-multi-agent-adapter-design`).

**Scope reminder (from the task itself):** this is a design/infrastructure
phase only. No new agent is implemented. No `StageHandler`, `FilterRegistry`,
`Pipeline`, tracking, or CLI runtime behavior changes. The deliverable is
this document, an ADR, and updates to `backlog.md`/`CHANGELOG.md`.

**Reconciling with ANTI_GOALS.md #12 ("No multi-agent support in V1... V2")
and QB-035's own standing guidance ("deliberately not scheduled... revisit
only after [usage] validation exists"):** neither is violated by this
document. Anti-goal #12 rules out *shipping support* for a second agent in
V1 — this phase ships no agent support at all, only a design. QB-035's own
"not yet" guidance is a product-prioritization call, not an architectural
one; it is repeated here verbatim so it isn't silently forgotten, but the
user has explicitly directed this specific design phase to begin now, and a
design document has no runtime cost or user-facing surface — it does not
contradict "wait for validation before *building*" in the way actually
implementing a second adapter would.

---

## 0. Summary

**The headline finding, established by reading every file this task named
before writing a line of design:** Quor's core — `quor/rewrite/`,
`quor/filters/` (`FilterRegistry`), `quor/pipeline/` (`Pipeline`,
`ContentMask`, all stages, `extract/`), and `quor/tracking/db.py` — is
**already 100% agent-agnostic**. Grepping all of them for `claude`/`agent`
turns up nothing but a few docstring/comment references to *callers*, never
a branch, a field, or a name check. `InvocationRecord` has no agent column.
`Pipeline.execute()` takes a `ContentMask`. `FilterRegistry.find()` takes a
bare string. None of them know or care what produced their input. This is
not a coincidental convenience — it is by construction, and it means
**QB-035A's entire job is scoped to `quor/adapters/`, `quor/__main__.py`,
and two CLI commands (`init`, `doctor`)**. Nothing else needs to move.

Three things discovered during research materially shaped this design,
recorded here rather than left implicit:

1. **The generalization was already planned, not invented today.**
   `quor/adapters/base.py` already declares a `HookAdapter` Protocol
   (`run_hook(self) -> None`) — unused anywhere in the codebase today (zero
   references outside its own definition). `PROJECT_BIBLE.md`'s original
   architecture diagram already labels `base.py` as "`HookAdapter` Protocol,
   `HookInput`, `HookOutput`" and `claude.py` as "Claude Code adapter" — a
   plural-capable shape was the intent from day one; only the reference
   implementation was ever built. This design formalizes and extends an
   existing, if dormant, extension point rather than introducing a new
   architectural layer from nothing.
2. **A working precedent for exactly this kind of extension already exists
   and already ships:** `quor.compression_stage`/`quor.plugin`
   entry-point-group discovery (`quor/pipeline/plugin_loader.py`, ADR-026).
   Cached, fail-open per entry, aggregated into a report `quor doctor`
   consumes. This design proposes a third group, `quor.hook_adapter`,
   discovered and reported the same way — no new discovery mechanism
   invented, the existing one reused.
3. **Empirical evidence the first real second-agent adapter may be cheaper
   than assumed.** `claude.py`/`claude_read.py` both strip a *doubled* UTF-8
   BOM before parsing, with an inline comment: "Cursor sends doubled BOM on
   Windows" — confirmed as a documented, known behavior in
   `PROJECT_BIBLE.md` item 9. This means Cursor's hook payload has already
   been observed, informally, to be close enough to Claude Code's own JSON
   shape that the *only* accommodation needed so far was BOM stripping —
   not a hint that Cursor's hook contract is identical (verifying that
   properly is explicitly out of scope here and flagged as a real risk in
   §10), but a concrete data point against assuming every future adapter
   needs a wholly novel payload model.

**No architectural conflict was found that blocks this design.** The one
real, non-trivial duplication found (between `claude.py` and
`claude_read.py`) is exactly the kind of "unnecessary duplication" the task
asked to be surfaced before changing anything — it is described in §1.3 and
resolved by the interface proposed in §3, not by any code change made in
this phase.

---

## 1. Current State Audit

### 1.1 Already agent-agnostic (zero changes needed)

| Component | Evidence |
|---|---|
| `quor/rewrite/classifier.py`, `rules.py` | Grep for `claude`/`Claude`: no matches. `rewrite_command(command: str) -> str \| None` — pure string function. |
| `quor/filters/registry.py` (`FilterRegistry`) | `find(command: str)`, `apply(filter_config, content: str)`, `trace(...)` — no agent parameter anywhere, no agent branch. |
| `quor/pipeline/engine.py` (`Pipeline`) | `execute(mask: ContentMask, ...)` — operates purely on `ContentMask`/`StageConfig`. |
| `quor/pipeline/stages/*.py` | Every `StageHandler.apply()` takes `(ContentMask, StageConfig)`. No stage has ever needed to know its caller. |
| `quor/pipeline/extract/registry.py` | `extract(file_path: Path) -> str \| None`, extension-routed. Docstring/comments *mention* `claude_read.py` as the current caller — never branches on it. |
| `quor/tracking/db.py` (`InvocationRecord`, `track_invocation`, `query_gain`) | `command: str`, `project_path: str`, token counts, `filter_name`, `was_passthrough` — no agent field. `command` is a free-text, adapter-chosen label (`"git status"`, `"Read: notes.md"`) that already doubles as an implicit source hint, but nothing downstream parses or requires that convention. |
| `quor/config/model.py` (`FilterConfig`, TOML schema) | No agent-scoping field of any kind. A filter matches a command string or is looked up by name — identical regardless of what produced the string. |

This table is the single most important artifact of this research pass:
**the "keep FilterRegistry/Pipeline/tracking agent-agnostic" requirement is
already satisfied by the existing codebase.** The design in §3 is careful
to preserve this — no proposed interface threads an agent identifier into
any of these modules.

### 1.2 Agent-specific by necessity (adapters — correctly scoped today)

`quor/adapters/claude.py` (PreToolUse/Bash) and `quor/adapters/claude_read.py`
(PostToolUse/Read) are, correctly, entirely Claude-Code-specific: they parse
Claude Code's literal JSON payload shape and emit Claude Code's literal
expected response shape (`hookSpecificOutput.updatedInput` /
`hookSpecificOutput.updatedToolOutput`, per ADR-030/ADR-034). This is
exactly what an adapter *should* own. The problem is not that this code is
agent-specific — it's that nothing formalizes the boundary of what "an
adapter" is, so the coupling below leaked one layer up into files that
should not have needed to know an agent's name at all.

### 1.3 Where agent-name branching actually leaked out (the real findings)

| File | Coupling found | Why it matters |
|---|---|---|
| `quor/__main__.py` | `_HOOK_ADAPTERS: frozenset[str] = frozenset({"claude", "claude-read"})` and `_run_hook()`'s `if adapter == "claude": ... else: # "claude-read" ...` — a hardcoded, closed set with an if/else that grows one branch per agent-event combination. | Directly the "avoid branching throughout the codebase on agent names" requirement. This is the one place in the whole codebase that does it today. |
| `quor/cli/commands/init.py` | `init(claude: bool = typer.Option(...))` — the *only* option is `--claude`. `settings_file = Path.home() / ".claude" / "settings.json"` is hardcoded. The entire `hooks.PreToolUse`/`hooks.PostToolUse` JSON shape, matcher strings (`"Bash"`, `"Read"`), and PowerShell-only script generation are Claude-Code-settings-schema-specific, inlined directly in the command function. | A second agent needs a different config file, a different JSON schema, quite possibly a different script language (not every agent necessarily shells out to PowerShell) — none of which fits today's function without another hardcoded branch, and CLAUDE.md's "exactly six commands" rule means this can't be solved by adding `quor init-cursor`. |
| `quor/cli/commands/doctor.py` | `_check_hook_script()`/`_check_read_hook_script()` hardcode the literal filenames `"claude-hook.ps1"`/`"claude-hook-read.ps1"`. `_check_hook_roundtrip()`/`_check_read_hook_roundtrip()` import `quor.adapters.claude`/`quor.adapters.claude_read` directly and construct Claude-Code-shaped synthetic payloads inline. `doctor()`'s own check list is a flat, hardcoded sequence of Claude-specific function calls. | `ROADMAP.md` v2.0 already names "Adapter detection in `quor doctor`" as required multi-agent work — this file is exactly where that has to land, and today there is no seam to hang it on. |
| `quor/adapters/base.py` | `HookInput`/`ToolInput`/`HookSpecificOutput`/`HookOutput` are named generically but their *field shapes* are Claude-Code-specific (`tool_input.command`, `updatedInput`, `permissionDecision`). The one genuinely generic thing here, `HookAdapter`, is an unused, minimal `run_hook(self) -> None` Protocol — too thin to describe install/doctor/multi-event behavior, and not implemented by either existing adapter today. | This is the extension point everything else should be built from — see §3. |

### 1.4 Real duplication between the two existing adapters — surfaced, not fixed here

Per the task's explicit instruction to stop and explain duplication before
changing anything: `claude.py` and `claude_read.py` independently
re-implement the same shape of boilerplate:

- Identical UTF-8 BOM-stripping line (`raw.lstrip(_UTF8_BOM)`), with the
  identical `_UTF8_BOM` constant, defined twice.
- Structurally parallel hook-script templates (`HOOK_COMMAND`/
  `HOOK_PS1_TEMPLATE` vs. `HOOK_READ_COMMAND`/`HOOK_READ_PS1_TEMPLATE`) —
  same PowerShell wrapper shape, different embedded `quor hook <name>`
  invocation.
- Both read `sys.stdin` and write `sys.stdout.buffer` directly inside
  `run_hook()`, making every existing test for both adapters monkeypatch
  `sys.stdin`/`sys.stdout` to exercise them (visible throughout
  `test_adapters.py`, `test_adapters_read.py`, `test_read_hook_activation.py`).

This is real, but it is *not* a bug and *not urgent* — both files are
correct and well-tested today. It is exactly the kind of duplication that
becomes actively harmful once a third and fourth adapter arrive (each
would otherwise re-copy the BOM-stripping line and the sys.stdin/stdout
plumbing a third and fourth time). §3.3 proposes the interface shape that
retires this duplication as part of the QB-035B migration — not by editing
`claude.py`/`claude_read.py` in this phase, which the task explicitly
forbids ("Do not implement any runtime behavior").

---

## 2. Design Principles (carried over from existing, proven precedent)

Rather than inventing new architectural idioms, this design deliberately
reuses three patterns Quor has already shipped and battle-tested:

1. **Protocol, not ABC, for the extension point** — exactly like
   `StageHandler` and `Plugin`. `runtime_checkable`, minimal, class-level
   `api_version`/identifier attributes, structural typing (a conforming
   class doesn't need to import or subclass anything from `quor.adapters`).
2. **Two-tier discovery: a hardcoded built-in dict, plus an optional
   entry-point group for third parties** — exactly like
   `_STAGE_HANDLERS`/`quor.compression_stage` and
   `PluginRegistry`/`quor.plugin`. Built-in agents (Claude Code first) never
   need Quor to "install a plugin for itself"; third-party agent adapters
   (a community-maintained Cursor/Copilot package, for instance) get the
   same fail-open, cached discovery every other extension point already has.
3. **Fail-open at every layer, no new exception type strictly required** —
   `HookError`/`ConfigError` (`quor/errors.py`) already model "a hook/config
   problem, non-fatal to the process." The existing outer guard in
   `__main__._run_hook()` (catch-all → return original stdin bytes) already
   generalizes to "whichever adapter was selected raised" without
   modification.

CLAUDE.md's "no speculative abstractions" rule was treated as a hard
constraint throughout: every abstraction introduced below is justified by
something concrete already observed in the two existing adapters (not
imagined future agent needs), and the one place genuine uncertainty exists
(exactly what event models Cursor/Copilot/Gemini expose) is treated as an
open, explicitly-flagged risk rather than designed around speculatively.

---

## 3. Proposed Architecture

### 3.1 `AgentEvent` — an abstract event-kind enum

Claude Code's hook model has two event shapes Quor currently integrates
with: "intercept a command before it runs" (`PreToolUse`/`Bash`) and
"replace content a tool already produced" (`PostToolUse`/`Read`). Quor's
adapters should reason about *these two abstract capabilities*, not
Claude Code's specific event names — a future agent may call its
equivalent something else entirely, or expose only one of the two.

```python
class AgentEvent(StrEnum):
    COMMAND_INTERCEPT = "command_intercept"
    """Before a shell command runs, the adapter may rewrite it.
    Maps to Claude Code's PreToolUse/Bash today."""

    CONTENT_INTERCEPT = "content_intercept"
    """After a tool already produced content (e.g. a file read), the
    adapter may replace it before the agent's model sees it.
    Maps to Claude Code's PostToolUse/Read today."""
```

Deliberately a **closed, minimal set of two** — not an open/free-text
event system. Both values are already implemented today, under different
names; nothing here is speculative. A third event kind (e.g. a
prompt-submission intercept some future agent might expose) is an additive
enum member plus one new optional Protocol method — never a breaking
change to an adapter that doesn't implement it, mirroring how
`PluginCategory` already has exactly three fixed values and has never
needed a fourth.

### 3.2 `AgentAdapter` — the runtime + lifecycle Protocol

```python
@runtime_checkable
class AgentAdapter(Protocol):
    """One AI coding agent's integration with Quor. Implemented once per
    agent (Claude Code, and — not in this phase — Cursor, Copilot, Gemini).
    """

    agent_id: ClassVar[str]
    """Stable identifier: "claude", "cursor", ... Used in `quor hook
    <agent_id> <event>` routing, `quor init --agent <agent_id>`, and every
    `quor doctor` line this adapter contributes."""

    display_name: ClassVar[str]
    """Human-readable name shown in `quor doctor`/`quor init` output."""

    api_version: ClassVar[int]
    """Mirrors QUOR_PLUGIN_API_VERSION's contract: adapters declaring a
    newer major version than Quor's own are rejected at discovery time."""

    @property
    def supported_events(self) -> frozenset[AgentEvent]:
        """Which AgentEvent kinds this agent's hook model actually exposes.
        Drives both `quor init` (which hooks to install) and `quor doctor`
        (which roundtrip checks are even meaningful to run)."""
        ...

    def handle_event(
        self, event: AgentEvent, raw_stdin: bytes, tracking: TrackingDB | None
    ) -> bytes | None:
        """Process one hook invocation for `event`. `raw_stdin` is the
        untouched original payload bytes (BOM and all — stripping is each
        adapter's own concern, exactly as today). Returns the raw response
        bytes to write to stdout, or None if `event` is not in
        `supported_events` (treated as "not handled," identical in spirit
        to StageHandler.can_handle() returning False).

        Must not raise for expected failure modes (malformed payload,
        missing optional dependency, ...) — those are this adapter's own
        fail-open responsibility, exactly as `claude.py`/`claude_read.py`
        already implement today. An unexpected exception is still caught by
        the existing outer guard in `__main__._run_hook()`, which returns
        `raw_stdin` unchanged — this is unchanged, existing behavior, not
        new to this design."""
        ...

    def install(self, ctx: InstallContext) -> InstallResult:
        """Write this agent's hook script(s) and register them with the
        agent's own config mechanism. Mirrors `init.py`'s existing
        dry-run → confirm → atomic-write flow, generalized: this method
        owns the agent-specific *content* (script template, config file
        path/schema); the CLI command owns the agent-agnostic *flow*
        (console output, confirmation prompt, calling `doctor` afterward)."""
        ...

    def doctor_checks(self, ctx: DoctorContext) -> list[DoctorCheck]:
        """Return this adapter's own (name, ok, detail) checks — replaces
        `doctor.py`'s hardcoded `_check_hook_script`/`_check_hook_roundtrip`/
        etc. `doctor()` itself becomes `for adapter in registry: checks +=
        adapter.doctor_checks(ctx)`, agent-agnostic."""
        ...
```

`DoctorCheck` is simply the existing `tuple[str, bool, str]` shape
`doctor.py` already returns from every `_check_*` function today, given a
name for reuse across files: `DoctorCheck = tuple[str, bool, str]` (a type
alias, not a new class — no behavior change, purely for readability at the
new call sites).

`InstallContext`/`InstallResult` and `DoctorContext` are deliberately thin,
`kw_only` frozen dataclasses (mirroring `PluginContext`'s own documented
rationale: "intentionally lean... without being coupled to Quor's internal
implementation"):

```python
@dataclass(frozen=True, kw_only=True)
class InstallContext:
    settings_override: Path | None   # test hook, mirrors init.py's existing --settings-path
    yes: bool                        # skip confirmation, mirrors init.py's existing --yes
    dry_run_console: Console         # for the existing "Dry run" preview output

@dataclass(frozen=True, kw_only=True)
class InstallResult:
    installed_paths: tuple[Path, ...]
    warnings: tuple[str, ...]        # e.g. "another tool's hook is already registered"

@dataclass(frozen=True, kw_only=True)
class DoctorContext:
    settings_override: Path | None   # mirrors doctor.py's existing --settings-path
```

### 3.3 Why `handle_event` takes/returns `bytes`, not `None` with direct I/O

Today, `run_hook() -> None` reads `sys.stdin` and writes `sys.stdout.buffer`
itself — which is why every existing adapter test monkeypatches both
streams (see §1.4). Proposing `handle_event(event, raw_stdin: bytes,
tracking) -> bytes | None` moves stream I/O to exactly one place —
`__main__._run_hook()` — which already reads `sys.stdin.buffer` and holds
`original_bytes` for its fail-open path today. This is a real interface
improvement, not I/O ceremony for its own sake:

- **Removes the duplicated BOM-stripping site.** One place strips it (or,
  more precisely, each adapter still owns whether/how it strips BOM from
  the bytes it receives — but no adapter needs to touch `sys.stdin` to do
  so).
- **Makes every adapter trivially unit-testable** with a plain
  `assert adapter.handle_event(EVENT, payload_bytes, None) == expected_bytes`
  — no `io.TextIOWrapper`/`_FakeStdout` scaffolding required at the adapter
  level (that scaffolding still exists at the `__main__._run_hook()`
  integration-test layer, exactly as it does today, since something must
  still verify the real stdin/stdout contract end to end).
- **Matches the existing outer fail-open guard exactly as-is** — `__main__.
  _run_hook()` already does `original_bytes = sys.stdin.buffer.read()` then
  a `try/except` that falls back to writing `original_bytes`; calling
  `adapter.handle_event(event, original_bytes, tracking)` inside that same
  `try` needs zero change to the guard itself.

### 3.4 `AdapterRegistry` — discovery, mirroring `plugin_loader.py` exactly

`quor/adapters/registry.py` (new module, not written in this phase):

```python
_BUILTIN_ADAPTERS: dict[str, type[AgentAdapter]] = {
    "claude": ClaudeAdapter,   # QB-035B: wraps today's claude.py/claude_read.py
}
_ADAPTER_EP_GROUP = "quor.hook_adapter"

class AdapterRegistry:
    def all_adapters(self) -> list[tuple[str, AgentAdapter]]: ...  # (tier, instance)
    def find(self, agent_id: str) -> AgentAdapter | None: ...
```

Structurally identical to `_STAGE_HANDLERS` (`quor/filters/registry.py`)
for the built-in half, and to `discover_plugins()`/`get_extra_stage_handlers()`
(`quor/pipeline/plugin_loader.py`) for the entry-point half: cached
discovery, one `warnings.warn()` + skip per failed entry, never a total
failure from one broken third-party adapter. `quor doctor`'s existing
`_check_plugins()` pattern (report `stages`/`plugins`/`failures`) extends
naturally to a fourth report field, `adapters`.

### 3.5 `__main__.py` — routing generalized

Today: `quor hook claude` / `quor hook claude-read` (agent identity and
event kind baked into one opaque string, one new branch per combination).

Proposed: `quor hook <agent_id> <event>` — two orthogonal argv positions,
resolved via the registry:

```python
registry = AdapterRegistry()
adapter = registry.find(agent_id)
if adapter is None or event not in adapter.supported_events:
    # unchanged fail-open: write original_bytes, warn to stderr
    ...
else:
    result = adapter.handle_event(AgentEvent(event), original_bytes, tracking)
    sys.stdout.buffer.write(result if result is not None else original_bytes)
```

This is a **backward-compatibility-sensitive change**, called out
explicitly in §9/§10: every hook script already installed on a user's
machine invokes the *old* two-argv-token form (`quor hook claude`). The
migration plan does not silently break those — see §9.

### 3.6 CLI generalization (`init`, `doctor`) — no seventh command

`quor init` keeps `--claude` as a recognized flag (so already-documented
muscle memory keeps working) but it becomes sugar for a new, generic
`--agent <agent_id>` option that resolves through the registry and calls
`adapter.install(ctx)`. `doctor()` replaces its hardcoded call sequence
with a loop over `registry.all_adapters()`, calling `adapter.doctor_checks(ctx)`
for each and flattening the results into the existing `checks` list — the
printed output format (`✓`/`✗` + name + detail) is unchanged, only *which
function produced each row* changes. Neither command grows past its
existing name; CLAUDE.md's "exactly six commands" rule is respected by
construction, not by exception.

`quor explain` is **not** touched by this design and is flagged as a real,
currently-unaddressed gap in §10 — it only knows how to explain a Bash
command string via `FilterRegistry.find()` + a live subprocess run; there
is no equivalent today for "explain how a CONTENT_INTERCEPT-shaped event
for this file would be compressed." Worth a dedicated future item (§11),
not solved here.

---

## 4. Adapter Lifecycle

An `AgentAdapter`'s lifecycle is genuinely different from `Plugin`'s, and
the design deliberately does **not** copy `Plugin`'s `initialize()`/
`shutdown()` shape — explained here rather than left as an unexplained
asymmetry:

- **`Plugin.initialize(ctx)`/`shutdown()`** exist because a `Plugin`
  instance is constructed once and reused for the lifetime of *one
  dispatcher invocation* (`run_dispatch()` builds a `PluginRegistry`,
  initializes every plugin once, runs `PRE_FILTER`/`POST_FILTER` possibly
  multiple times conceptually, then shuts down) — there is real
  cross-call state worth setting up once (a network connection, a loaded
  config file).
- **An `AgentAdapter.handle_event()` call has no such reuse opportunity at
  all.** Every real hook invocation is `python -m quor hook <agent> <event>`
  spawned as a **brand-new OS process** by the AI agent itself — there is
  no in-memory state to initialize once and reuse, because there is no
  "again" within one process. `handle_event()` must therefore be a pure,
  stateless, single-shot call: parse → transform (via the already-agnostic
  core: `rewrite_command()`, `FilterRegistry`, `extract()`,
  `track_invocation()`) → serialize.

The three timeframes an `AgentAdapter` actually participates in:

1. **Install-time** (`quor init --agent X`) — one-shot, interactive,
   side-effecting (writes files, edits the agent's own config). Not
   performance-sensitive; today's `init.py` already budgets for a
   confirmation prompt and a live `quor doctor` run afterward.
2. **Event-time** (`quor hook X <event>`) — the hot path. Must stay inside
   whatever timeout budget the *agent* enforces on its own hook mechanism
   (Claude Code's is documented as ~30s end to end today, referenced in
   `explain.py`'s own subprocess timeout and `dispatcher.py`'s 25s
   subprocess budget). Stateless, single-shot, fail-open.
3. **Diagnostic-time** (`quor doctor`) — synchronous, read-only, in the
   *CLI's own process* (not a fresh hook invocation) — `doctor_checks()`
   can call `self.handle_event(...)` directly with a synthetic payload
   exactly as today's `_check_hook_roundtrip()`/`_check_read_hook_roundtrip()`
   already do, just relocated onto the adapter itself.

---

## 5. Public Interfaces (summary)

All types below are proposed additions to `quor/adapters/base.py` (or a new
`quor/adapters/types.py` if `base.py` grows too large — a QB-035B
implementation-time judgment call, not decided here):

```python
class AgentEvent(StrEnum):
    COMMAND_INTERCEPT = "command_intercept"
    CONTENT_INTERCEPT = "content_intercept"

DoctorCheck = tuple[str, bool, str]  # (name, ok, detail) — existing shape, now named

@dataclass(frozen=True, kw_only=True)
class InstallContext:
    settings_override: Path | None
    yes: bool

@dataclass(frozen=True, kw_only=True)
class InstallResult:
    installed_paths: tuple[Path, ...]
    warnings: tuple[str, ...]

@dataclass(frozen=True, kw_only=True)
class DoctorContext:
    settings_override: Path | None

@runtime_checkable
class AgentAdapter(Protocol):
    agent_id: ClassVar[str]
    display_name: ClassVar[str]
    api_version: ClassVar[int]

    @property
    def supported_events(self) -> frozenset[AgentEvent]: ...
    def handle_event(self, event: AgentEvent, raw_stdin: bytes, tracking: TrackingDB | None) -> bytes | None: ...
    def install(self, ctx: InstallContext) -> InstallResult: ...
    def doctor_checks(self, ctx: DoctorContext) -> list[DoctorCheck]: ...
```

`HookInput`/`ToolInput`/`HookSpecificOutput`/`HookOutput`/
`PostToolUseHookInput`/etc. (existing, Claude-Code-shaped models) are
**not renamed or moved** — they remain exactly what they are: the payload
models `ClaudeAdapter` uses internally to implement `handle_event()`. A
future `CursorAdapter` would define its own payload models next to its own
adapter module, not attempt to reuse Claude Code's — the whole point of the
`bytes`-in/`bytes`-out boundary in §3.3 is that no shared module needs to
know any agent's payload shape.

The existing, unused `HookAdapter` Protocol in `base.py`
(`run_hook(self) -> None`) is superseded by `AgentAdapter` above and should
be removed when `AgentAdapter` lands (QB-035B) — recorded here so its
removal isn't mistaken for an unrelated deletion later.

---

## 6. Extension Points

1. **Built-in adapters** — a hardcoded `dict[str, type[AgentAdapter]]` in
   `quor/adapters/registry.py`, exactly like `_STAGE_HANDLERS`. Claude Code
   is the only entry until a future QB-035 sub-item adds a second.
2. **Third-party adapters** — `quor.hook_adapter` entry-point group,
   discovered and cached exactly like `quor.compression_stage`/`quor.plugin`
   (`quor/pipeline/plugin_loader.py`). Lets a community package ship a
   Cursor/Copilot/Gemini adapter without Quor itself needing to depend on
   or vendor it — directly serving `ANTI_GOALS.md`'s existing "the
   `quor.compression_stage` entry-point API is AI-assistant-agnostic"
   commitment, extended to adapters.
3. **`file://` escape hatch** — `plugin_loader.load_from_file_uri()`
   already lets a stage type of `file:///path/to/module.py::ClassName` load
   without packaging, for local development. Whether `AgentAdapter` needs
   the same escape hatch (e.g. for a corporate team building an internal
   agent integration without publishing a package) is **not decided in this
   phase** — noted as an open question for QB-035B, since it's a small,
   additive extension of an already-proven mechanism, not a new one.

---

## 7. Failure Model

Every failure mode below already has a proven, shipped precedent elsewhere
in Quor — none is new:

| Failure | Handling | Precedent |
|---|---|---|
| `handle_event()` raises unexpectedly | Caught by `__main__._run_hook()`'s existing outer `try/except`; original stdin bytes written back, warning to stderr. | Unchanged from today's behavior for `claude.py`/`claude_read.py`. |
| Adapter discovery fails for one third-party entry point | `warnings.warn()` + skip that one adapter; every other adapter (built-in and third-party) is unaffected. | `plugin_loader.py`'s existing per-entry-point fail-open. |
| `install()` fails partway | Must not corrupt the agent's existing config file — reuse `init.py`'s existing tempfile+`os.replace()` atomic-write pattern for every file `install()` touches. | `init.py`'s `_write_text_atomic`/`_write_json_atomic`, unchanged. |
| `doctor_checks()` raises | The `for adapter in registry: ...` loop in `doctor()` wraps each adapter's call in its own `try/except`, reporting `(f"{adapter.display_name} checks", False, str(exc))` rather than aborting the whole `quor doctor` run. | Mirrors `_check_plugins()`'s existing "could not check" fallback branch. |
| An agent's hook mechanism doesn't support an `AgentEvent` the adapter is asked to handle | `handle_event()` returns `None` for events outside `supported_events`; caller treats it identically to "adapter not found." | Mirrors `StageHandler.can_handle()` returning `False`. |

No new exception type is strictly required — `HookError`/`ConfigError`
already model every case above. An `AdapterError` (mirroring `PluginError`,
for symmetry and a distinguishable warning prefix) is a reasonable
QB-035B addition but not architecturally necessary.

---

## 8. Testing Strategy

1. **Protocol conformance** — `isinstance(ClaudeAdapter(), AgentAdapter)`,
   mirroring `TestStageHandlerProtocol` in `tests/unit/test_pipeline.py`.
2. **Byte-for-byte behavioral equivalence** — the single most important
   test category for the QB-035B migration: every existing fixture in
   `tests/unit/test_adapters.py`, `test_adapters_read.py`,
   `test_read_hook_activation.py`, `test_read_hook_ast_summarization.py`,
   and `test_tracking.py`'s `TestReadTracking`/`TestReadSourceCodeTracking`
   must produce **identical** stdout bytes whether driven through today's
   `claude.py::run_hook()`/`claude_read.py::run_hook()` or through
   `ClaudeAdapter().handle_event(...)` — proven via a before/after diff
   harness, exactly the discipline QB-005B established for the AST parser
   framework refactor (14 fixtures, byte-for-byte, before any filter
   behavior was allowed to change).
3. **Registry discovery** — built-in dict lookup, entry-point discovery,
   fail-open per broken third-party adapter, cache invalidation on package
   set change — mirroring `tests/unit/test_plugin_loader.py` structurally.
4. **`quor doctor`/`quor init` integration** — generalized to assert "one
   row per registered adapter" rather than hardcoding Claude Code's two
   check names, so a future second adapter's checks are exercised by the
   same test without editing it.
5. **A synthetic second adapter in tests only** (never shipped) — a
   minimal `_FakeAgentAdapter` implementing `AgentAdapter` with a trivial
   `handle_event()`, used exactly like `test_pipeline.py`'s
   `_CompressAllStage`/`_NoOpStage` test doubles, to prove the registry and
   `__main__.py` routing genuinely support more than one adapter without
   needing a second *real* agent to exist yet.

---

## 9. Migration Strategy (phased — see §11 for backlog IDs)

Ordered so every step keeps the existing test suite green and ships no
observable behavior change until explicitly intended:

1. **Introduce, don't replace.** Add `AgentEvent`/`AgentAdapter`/
   `InstallContext`/`InstallResult`/`DoctorContext`/`AdapterRegistry` to
   `quor/adapters/`. Add `ClaudeAdapter`, a thin wrapper whose
   `handle_event()` bodies call the *existing* `claude.py`/`claude_read.py`
   module-level functions (refactored minimally to accept/return bytes
   instead of touching `sys.stdin`/`sys.stdout` directly, proven equivalent
   per §8.2). `__main__.py`, `init.py`, `doctor.py` are **not** touched yet
   — old hardcoded paths keep running unchanged, in parallel with the new,
   not-yet-wired registry. This is the safest possible increment: the new
   code is fully tested but inert.
2. **Migrate `__main__.py`'s routing** to resolve through
   `AdapterRegistry` instead of the hardcoded `_HOOK_ADAPTERS`/if-else.
   **Backward compatibility requirement:** every hook script already
   written to a user's disk by a prior `quor init --claude` invokes the
   *old* argv shape (`quor hook claude`, `quor hook claude-read`). Two
   options, to be decided at implementation time, not here: (a) keep
   `"claude"`/`"claude-read"` as permanent argv aliases that resolve to
   `("claude", COMMAND_INTERCEPT)`/`("claude", CONTENT_INTERCEPT)`, so old
   hook scripts keep working forever with zero user action; or (b) treat
   this as a breaking change requiring `quor init --claude` to be re-run
   post-upgrade, clearly communicated in `CHANGELOG.md` and surfaced by
   `quor doctor`. **(a) is the recommended default** — it costs one small,
   permanent alias table and avoids ever silently breaking an
   already-installed hook.
3. **Migrate `doctor.py`** to loop over `registry.all_adapters()`.
4. **Migrate `init.py`** to `--agent <agent_id>` (keeping `--claude` as
   sugar) and delegate to `adapter.install()`.
5. **Remove the now-superseded `HookAdapter` Protocol** from `base.py`.
6. **(Separately gated on real demand, per QB-035's own standing
   guidance)** implement a second real `AgentAdapter` — Cursor is the
   best-evidenced candidate given §0's BOM-compatibility observation, but
   its actual hook contract must be verified against real Cursor
   documentation/behavior first (§10), not assumed from one shared quirk.

None of steps 1–5 requires a second real agent to exist — they are pure
refactor-behind-an-interface work, independently valuable (they retire the
`claude.py`/`claude_read.py` duplication from §1.4 either way) even if a
second agent is never built.

---

## 10. Risks

1. **Policy/prioritization tension (not technical):** `ANTI_GOALS.md` #12
   and QB-035's own text both say wait for usage validation before
   *building* multi-agent support. This design phase doesn't build support,
   but the natural next step (QB-035B) starts touching real, currently
   Claude-Code-only files (`__main__.py`, `doctor.py`, `init.py`) — worth a
   deliberate go/no-go checkpoint before QB-035B starts, not an assumption
   that design approval implies migration approval.
2. **Hook argv backward compatibility** (§9 step 2) — the single largest
   concrete implementation risk. Getting this wrong silently breaks
   already-installed hooks on every existing user's machine with no error
   message, exactly the failure class ADR-029/ADR-030 were written to
   prevent for a different reason. Must be tested against a real, already-
   installed hook script, not just unit tests of the new routing code.
3. **Unverified target agents.** This design's `AgentEvent` abstraction
   (two event kinds) is derived entirely from Claude Code's own hook model
   plus one empirical, undocumented-elsewhere data point about Cursor's BOM
   behavior. Whether Cursor, Copilot Agent, or Gemini CLI expose anything
   resembling `COMMAND_INTERCEPT`/`CONTENT_INTERCEPT` — or any hook
   mechanism at all — is **not verified** by this phase and must not be
   assumed true when QB-035B+ picks a real second agent to implement.
   Mirrors QB-005C's own "mandatory pre-flight compatibility gate" —
   applied here to hook contracts instead of a parser library.
4. **Six-CLI-command constraint interacts with the `--agent` flag.** The
   design in §3.6 keeps `init`/`doctor` as the only touched commands
   specifically to avoid this risk, but implementation-time flag naming
   (`--agent cursor` vs. `--cursor` vs. something else) needs to stay
   consistent with `--claude`'s existing UX, not fragment it.
5. **`quor explain` has no `CONTENT_INTERCEPT` equivalent** — flagged, not
   solved. A user cannot today ask "how would Quor compress a Read of this
   file," only "how would Quor compress this Bash command." This gap
   predates this design (it exists for Claude Code alone already) but
   becomes more visible once a second agent's adapter might rely more
   heavily on `CONTENT_INTERCEPT`-shaped events.
6. **Test-suite migration cost.** §8.2's byte-for-byte equivalence
   requirement is real work, not a formality — `test_adapters.py`/
   `test_adapters_read.py`/`test_read_hook_activation.py`/
   `test_read_hook_ast_summarization.py` collectively contain well over 100
   tests built around today's `sys.stdin`/`sys.stdout`-monkeypatching
   convention; QB-035B's estimate should budget for updating (not
   necessarily rewriting) all of them, not just adding new ones.

---

## 11. Design Trade-offs

- **`bytes`-in/`bytes`-out `handle_event()` vs. keeping direct
  `sys.stdin`/`sys.stdout` I/O per adapter** — chosen: `bytes`-in/`bytes`-out
  (§3.3). More testable, retires the duplicated BOM-stripping site, cleaner
  separation of "parse this agent's payload" from "own the process's
  stdio" — at the real cost of a non-trivial, test-suite-touching refactor
  of two already-shipped, already-correct files.
- **Built-in dict + entry-point group (two discovery mechanisms) vs.
  entry-point-only** — chosen: mirror the existing
  `_STAGE_HANDLERS`/`quor.compression_stage` dual mechanism exactly, for
  consistency with two other extension points that already work this way,
  and because Quor supporting Claude Code shouldn't require Quor to depend
  on an installable plugin for its own reference integration.
- **Two closed `AgentEvent` values now vs. an open/free-text event system**
  — chosen: minimal closed set, additive extension later. An open string-
  keyed event system would be more "flexible" but is exactly the kind of
  speculative abstraction CLAUDE.md's Rule warns against — nothing today
  needs a third event kind, and adding one later is a non-breaking enum
  addition, not a migration.
- **Keeping `HookInput`/`ToolInput`/etc. Claude-Code-shaped and
  adapter-local, rather than generalizing them into shared "generic hook
  payload" models** — chosen: adapter-local. A shared generic payload model
  would have to either lowest-common-denominator every future agent's
  fields (fragile, breaks the moment two agents' shapes diverge in an
  unanticipated way) or grow an ever-expanding `extra="allow"` grab-bag.
  Each adapter owning its own Pydantic models, with only the `bytes`
  boundary shared, is the same lesson QB-035's own "avoid branching on
  agent names" requirement teaches applied one layer deeper: don't just
  avoid `if agent == "claude"` in control flow, avoid it in data shape too.

---

## 12. ADR

See `docs/final/DECISIONS.md`, **ADR-036: Multi-Agent Adapter Architecture
— `AgentAdapter` Protocol + Registry (QB-035A)**, added alongside this
document. It records the decision itself (Protocol + two-tier registry,
`bytes`-in/`bytes`-out, two closed `AgentEvent` values) and the options
considered, in the same format as ADR-034/ADR-035.

---

## 13. Every File That Would Eventually Need Modification

Compiled directly from §1's audit, for a future implementer to start from
without re-deriving it. **None of these are modified in this phase.**

**New files (QB-035B):**
- `quor/adapters/registry.py` — `AdapterRegistry`, `_BUILTIN_ADAPTERS`,
  `quor.hook_adapter` entry-point discovery.
- `quor/adapters/claude_adapter.py` (or similar) — `ClaudeAdapter` class
  wrapping today's `claude.py`/`claude_read.py` behind `AgentAdapter`.

**Modified — core adapter layer (QB-035B):**
- `quor/adapters/base.py` — add `AgentEvent`, `AgentAdapter`,
  `InstallContext`, `InstallResult`, `DoctorContext`, `DoctorCheck`; remove
  the unused `HookAdapter` Protocol.
- `quor/adapters/claude.py` — `run_hook()` refactored to separate
  stdin/stdout I/O from parse-and-respond logic, so `ClaudeAdapter.
  handle_event()` can call the latter directly with `bytes` in/out.
- `quor/adapters/claude_read.py` — same refactor, same reasoning.

**Modified — routing and CLI (QB-035C/D/E, per the phased plan in §9):**
- `quor/__main__.py` — `_run_hook()` resolves via `AdapterRegistry`
  instead of the hardcoded `_HOOK_ADAPTERS` set + if/else; argv shape
  changes to `quor hook <agent_id> <event>` with back-compat aliases for
  `claude`/`claude-read`.
- `quor/cli/commands/doctor.py` — hardcoded `_check_hook_script`/
  `_check_hook_roundtrip`/`_check_read_hook_script`/
  `_check_read_hook_roundtrip`/`_check_hook_collision` replaced by a loop
  over `registry.all_adapters()` calling `adapter.doctor_checks(ctx)`.
- `quor/cli/commands/init.py` — `--claude` becomes sugar for a new
  `--agent <agent_id>` option; the settings.json-specific read/write/merge
  logic (`_read_settings`, `_install_hook_entry`, `_install_read_hook_entry`,
  `_find_conflicting_hooks`, `_write_text_atomic`, `_write_json_atomic`)
  either moves into `ClaudeAdapter.install()` or stays as shared helpers
  `ClaudeAdapter` calls — an implementation-time call, not decided here.

**Not modified, but flagged as a related future gap (not part of any
QB-035 sub-item unless separately scoped):**
- `quor/cli/commands/explain.py` — no `CONTENT_INTERCEPT` equivalent
  exists; out of scope for QB-035A–E as designed.

**Test files needing updates (QB-035B, per §8):**
- `tests/unit/test_adapters.py`, `test_adapters_read.py`,
  `test_read_hook_activation.py`, `test_read_hook_ast_summarization.py` —
  extended with `ClaudeAdapter`-level tests; existing tests kept passing
  unmodified as the equivalence proof, not rewritten wholesale.
- New: `tests/unit/test_adapter_registry.py` (mirrors
  `test_plugin_loader.py`), `tests/unit/test_agent_adapter_protocol.py`
  (mirrors `TestStageHandlerProtocol`).
- `tests/unit/test_cli.py` — `doctor`/`init` tests generalized to assert
  "one section per registered adapter" rather than hardcoding Claude Code.

**Documentation (this phase — QB-035A):**
- `docs/design/QB-035A-multi-agent-adapter-design.md` (this document).
- `docs/final/DECISIONS.md` — ADR-036.
- `backlog.md`, `CHANGELOG.md` — this design phase's entry.

**Documentation (future phases, not this one):**
- `docs/final/CLAUDE.md` — "Architecture at a Glance" diagram and
  `quor/adapters/` folder-responsibility row need updating once
  `AgentAdapter` actually exists in code.
- `docs/final/PROJECT_BIBLE.md` — architecture diagram (§ "Architecture
  Overview") currently shows the single-adapter flow; would need a second
  branch once a real second adapter ships.
- `docs/final/ANTI_GOALS.md` #12 — should be explicitly marked
  superseded/updated once multi-agent support actually ships (not before).
- `docs/final/ROADMAP.md` — v2.0's "Multi-agent support" bullets can start
  linking to concrete QB-035 sub-items once they exist.

---

## 14. Remaining Work — Phased Backlog Items

- **QB-035B** — Implement `AgentEvent`/`AgentAdapter`/`AdapterRegistry` +
  `ClaudeAdapter` (wrapping existing behavior, byte-for-byte equivalence
  proven). No routing/CLI changes. Safest, fully independent increment.
- **QB-035C** — Migrate `__main__.py` hook routing to the registry, with
  the back-compat alias decision from §9 step 2 made explicit and tested
  against a real pre-existing hook script.
- **QB-035D** — Migrate `quor doctor` to the per-adapter `doctor_checks()`
  loop.
- **QB-035E** — Migrate `quor init` to `--agent`, retire the Claude-specific
  inline logic in `init.py` into `ClaudeAdapter.install()`, remove the now-
  dead `HookAdapter` Protocol.
- **QB-035F** (gated on explicit product go-ahead, not automatic) —
  Verify a real second agent's actual hook contract (Cursor is the
  best-evidenced starting candidate per §0) and implement its
  `AgentAdapter` as the first proof this abstraction holds for more than
  one agent. This is the item ANTI_GOALS.md #12 actually names as V2 work
  — everything QB-035B–E above is infrastructure that has value even if
  QB-035F never happens.
- **(Unscoped, flagged not filed)** — `quor explain`'s
  `CONTENT_INTERCEPT` gap (§10.5); an optional `AdapterError` exception
  type (§7); whether `AgentAdapter` needs a `file://` escape hatch (§6.3).
