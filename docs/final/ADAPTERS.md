# Quor Multi-Agent Adapter Architecture

Implements QB-068 and QB-069, on the design shipped in QB-035A
(`docs/design/QB-035A-multi-agent-adapter-design.md`, ADR-036, ADR-040,
ADR-041). This is the canonical reference for `quor/adapters/` going
forward — read it before touching that package or adding a new agent
integration.

## Why this exists

Quor started as "a Claude Code tool." Its compression core — the rewrite
classifier, `FilterRegistry`, the `ContentMask` pipeline, `TrackingDB` — was
always agent-agnostic (see §1 of the QB-035A design doc for the file-by-file
audit that established this). Only `quor/adapters/`, `quor/__main__.py`'s
hook routing, and two CLI commands (`init`, `doctor`) ever knew Claude Code's
name. QB-068 formalizes that boundary into a real extension point so a
second, third, or Nth AI coding tool plugs into the same engine without
Quor's core ever branching on which one is calling it.

**Zero behavior change for existing users.** Every Claude Code hook script
already installed on disk keeps working with no user action — see
"Backward compatibility" below.

## The core abstraction: `AgentAdapter`

`quor/adapters/base.py`:

```python
class AgentEvent(StrEnum):
    COMMAND_INTERCEPT = "command_intercept"   # rewrite a shell command before it runs
    CONTENT_INTERCEPT = "content_intercept"   # replace content a tool already produced

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

A `runtime_checkable` `Protocol`, not an ABC — structural typing, matching
`StageHandler` and `Plugin`, Quor's two other extension points. A
conforming class needs no inheritance from anything in `quor.adapters`.

**`AgentEvent` is a closed, two-value set on purpose.** Both values already
exist today, under Claude Code's own names (`PreToolUse`/Bash,
`PostToolUse`/Read) — this is not speculative. A third event kind is a
non-breaking additive enum member later, never a redesign; see ADR-036 for
why an open/free-text event system was rejected.

**`handle_event()` is bytes-in/bytes-out — no stream I/O.** `raw_stdin` is
the untouched original payload bytes (BOM and all; stripping is each
adapter's own concern). The return value is the raw response bytes to write
to stdout, or `None` if the adapter doesn't handle this event. Exactly one
place in the entire codebase touches `sys.stdin`/`sys.stdout` for hooks:
`quor/__main__.py::_run_hook()`. This is what makes every adapter trivially
unit-testable (`adapter.handle_event(EVENT, payload_bytes, None) == expected`,
no stdio monkeypatching) and retires the duplicated BOM-stripping/stdio
boilerplate the two original Claude Code hooks each carried independently.

## Lifecycle — three distinct timeframes

An `AgentAdapter` is never a long-lived object reused across hook calls —
there is no "again" to optimize for within one process, because every real
hook invocation is `python -m quor hook <agent> <event>` spawned as a
**brand-new OS process** by the AI agent itself. This is why `AgentAdapter`
does *not* copy `Plugin`'s `initialize()`/`shutdown()` shape — there is no
cross-call state worth setting up once.

1. **Install-time** — `quor init --agent <id>` (or `--claude`, permanent
   sugar for `--agent claude`). One-shot, interactive, side-effecting:
   writes hook script(s), registers them in the agent's own config file.
   Not performance-sensitive.
2. **Event-time** — `quor hook <agent> <event>`. The hot path. Every call
   must stay inside whatever timeout budget the *agent* enforces on its own
   hook mechanism. `handle_event()` is pure, stateless, single-shot: parse
   → transform (via the agent-agnostic core: `rewrite_command()`,
   `FilterRegistry`, `extract()`, `track_invocation()`) → serialize.
3. **Diagnostic-time** — `quor doctor`. Synchronous, read-only, runs inside
   the CLI's own process (not a fresh hook invocation) — `doctor_checks()`
   can call `self.handle_event(...)` directly with a synthetic payload to
   prove the response logic actually works, independent of whether the
   agent is installed.

Every method must not raise for *expected* failure modes (malformed
payload, missing optional dependency, an unsupported event). An unexpected
exception from `handle_event()` is still caught by
`__main__._run_hook()`'s existing outer guard, which writes back the
original bytes unchanged — fail-open is preserved exactly as before, at the
same single layer it always lived at.

## Discovery: `AdapterRegistry`

`quor/adapters/registry.py` — two tiers, structurally identical to
`quor/pipeline/plugin_loader.py`'s existing `quor.compression_stage`/
`quor.plugin` groups:

1. **Built-in adapters** — a hardcoded `dict[str, type[AgentAdapter]]`.
   Quor's own reference integrations (Claude Code, Codex CLI, Gemini CLI)
   never need to be an installable plugin of themselves.
2. **Third-party adapters** — the `quor.hook_adapter` entry-point group,
   discovered fail-open per entry (one broken third-party adapter never
   prevents another — built-in or third-party — from loading). A built-in
   `agent_id` always wins over a same-named third-party entry point.

```python
from quor.adapters.registry import AdapterRegistry

registry = AdapterRegistry()
registry.find("claude")       # -> ClaudeAdapter instance, or None
registry.all_adapters()       # -> list[AgentAdapter], built-ins first
registry.failures             # -> list[AdapterFailureInfo], populated after discovery
```

`quor/__main__.py`'s `_run_hook()`, `quor/cli/commands/doctor.py`'s
`_check_adapters()`, and `quor/cli/commands/init.py`'s
`_init_generic_agent()` are the only three call sites — routing, health
checks, and installation all resolve through the same registry, none of
them hardcode an agent's name.

## Backward compatibility: the hook argv shape

Every hook script a prior `quor init --claude` wrote to disk invokes the
old, two-argv-token form: `quor hook claude` / `quor hook claude-read`.
`quor/__main__.py` keeps these as **permanent aliases**:

```python
_HOOK_ARGV_ALIASES = {
    "claude": ("claude", "command_intercept"),
    "claude-read": ("claude", "content_intercept"),
}
```

resolved to `(agent_id, event)` pairs before hitting the registry. An
already-installed hook keeps working forever with zero user action. New
installs (`quor init --agent <id>`) use the general
`quor hook <agent_id> <event>` shape directly.

## Current adapters

| Adapter | `agent_id` | `supported_events` | Status |
|---|---|---|---|
| `ClaudeAdapter` | `claude` | `COMMAND_INTERCEPT`, `CONTENT_INTERCEPT` | Full — unchanged from pre-QB-068 behavior, just wrapped |
| `GeminiAdapter` | `gemini` | `COMMAND_INTERCEPT` | Command rewriting only |
| `CodexAdapter` | `codex` | *(none)* | Detection/readiness reporting only |
| `CursorAdapter` | `cursor` | *(none)* | Detection/readiness reporting only |
| `VSCodeAdapter` | `vscode` | *(none)* | Detection/readiness reporting only (targets Copilot agent mode) |
| `WindsurfAdapter` | `windsurf` | *(none)* | Detection/readiness reporting only |
| `AiderAdapter` | `aider` | *(none)* | Detection/readiness reporting only |
| `ContinueAdapter` | `continue` | *(none)* | Detection/readiness reporting only |

None of this is guesswork — every capability claim above (and every
capability *not* claimed) is grounded in each adapter's own module
docstring, researched against the tool's live documentation before writing
any code, per ADR-036's own explicit risk (§10.3: "unverified target agents
must not be assumed... mirrors QB-005C's own mandatory pre-flight
compatibility gate"). Re-verify against the tool's current docs before
extending scope, not from memory of this document.

**QB-069's headline finding:** researching Cursor, VS Code (Copilot agent
mode), Windsurf (Cascade), Aider, and Continue.dev — five more tools, live,
before writing any code — found the same answer five times in a row that
Codex CLI (QB-068) found first: **none has a confirmed way to rewrite a
command before it runs, or replace a tool's output before the model sees
it.** Every one of these six tools' pre-execution hooks (where they exist
at all) is allow/deny/block-only; every post-execution hook (where one
exists at all) is observational-only; two of the six (Aider, Continue.dev)
have no tool-call hook system whatsoever. This is a real, converging
industry pattern, not six independent coincidences — see each adapter's own
module docstring for the tool-specific evidence, and
`quor/adapters/_detection_only.py`'s module docstring for the full
side-by-side summary.

### `ClaudeAdapter` (`quor/adapters/claude_adapter.py`)

A thin wrapper: `handle_event()` delegates to
`quor.adapters.claude.handle_bytes()` / `quor.adapters.claude_read.
handle_bytes()` — the exact pre-QB-068 hook logic, now exposed as
bytes-in/bytes-out core functions alongside the original `run_hook()`
(unchanged, still what direct tests and any external caller exercise).
`install()`/`doctor_checks()` delegate to `init.py`/`doctor.py`'s existing
machinery. `tests/unit/test_claude_adapter_equivalence.py` proves
byte-for-byte identical output to the pre-refactor hooks across every case
that matters (rewrite, no-op, BOM handling, Read compression, Read no-op).

### `GeminiAdapter` (`quor/adapters/gemini_adapter.py`)

Gemini CLI's `BeforeTool` hook is confirmed (via Gemini CLI's own docs) to
support rewriting a tool's arguments via `hookSpecificOutput.tool_input`,
matched against the `run_shell_command` tool — the Gemini equivalent of
Claude Code's `hookSpecificOutput.updatedInput`. This adapter installs a
PowerShell hook script (same Windows-first pattern as Claude's) registered
under `~/.gemini/settings.json`'s `hooks.BeforeTool`.

**Not implemented, and why:** `CONTENT_INTERCEPT`. Gemini CLI's `AfterTool`
hook's only confirmed output capability is `additionalContext` (append) —
no confirmed full-content-replace field equivalent to Claude's
`updatedToolOutput`. Adding it later is a non-breaking, additive change
once/if a replace-capable field is confirmed upstream.

**Known limitation:** the exact `tool_input` field name (`command`) is
inferred from Gemini CLI's general tool schema, not a hooks-specific worked
example, and end-to-end behavior against a real Gemini CLI session is not
verified by this implementation (only `handle_bytes()`'s own logic is —
`quor doctor`'s roundtrip check proves Quor's response shape is correct,
not that the installed Gemini CLI binary honors it — exactly the same
caveat ADR-034 already documents for Claude's own Read hook).

### Detection-only adapters — shared base (`quor/adapters/_detection_only.py`)

Six adapters (`CodexAdapter`, `CursorAdapter`, `VSCodeAdapter`,
`WindsurfAdapter`, `AiderAdapter`, `ContinueAdapter`) share one base class,
`DetectionOnlyAdapter`, instead of six near-identical copies of the same
`supported_events = frozenset()` / advisory-doctor-checks / no-op-`install()`
shape `CodexAdapter` originated in QB-068. A concrete subclass supplies
exactly three things:

```python
class SomeAdapter(DetectionOnlyAdapter):
    agent_id: ClassVar[str] = "some_id"
    display_name: ClassVar[str] = "Some Tool"
    limitation_reason: ClassVar[str] = "... this tool's own specific reason ..."

    def _detect(self) -> tuple[bool, str]:
        ...  # deterministic filesystem/PATH check only
```

Every `AgentAdapter` method — `supported_events`, `handle_event()`,
`install()`, `doctor_checks()` — is implemented once, in the base class.
`install()` writes nothing and returns a warning containing
`limitation_reason`; `doctor_checks()` returns exactly two checks
(`"<name> detected"`, `"<name> hook integration"`), both always advisory
(`ok=True`) regardless of detection outcome — see "`quor doctor`/`quor
init` behavior for new, optional adapters" below for why. This is tested
once, generically, by `tests/unit/test_agent_adapter_protocol.py::
TestDetectionOnlyAdapterSharedContract` (parametrized across all six) and
in isolation by `tests/unit/test_detection_only_adapter.py` — not
re-tested per adapter. **Grouping the shape does not imply the reason is
identical** — `limitation_reason` and `_detect()` differ per tool, and each
adapter's own module docstring is the authoritative source for its own
finding, not this shared base.

#### `CodexAdapter` (`quor/adapters/codex_adapter.py`)

Codex's `PreToolUse` hook is documented as able to allow/deny a Bash call,
with no confirmed way to rewrite it; Codex's hook system is also
experimental with unconfirmed Windows support, a material risk for a
Windows-first tool. Detects via `~/.codex`.

#### `CursorAdapter` (`quor/adapters/cursor_adapter.py`)

Cursor's `beforeShellExecution`/`beforeMCPExecution` hooks (`.cursor/
hooks.json`) are documented allow/deny/ask only — the stdout response
schema is `{"continue": bool, "permission": "allow"|"deny"|"ask"}`, with no
field for a rewritten command. Cursor has no post-execution or post-read
hook at all (`afterFileEdit` fires after the *agent writes* a file, the
reverse direction). Detects via `~/.cursor`.

#### `VSCodeAdapter` (`quor/adapters/vscode_adapter.py`)

Targets VS Code's bundled GitHub Copilot agent mode specifically (vanilla
VS Code has no AI agent of its own) — a scope note recorded in the
adapter's own docstring and enforced by a regression test
(`display_name` must mention "Copilot"). Its `PreToolUse`/`PostToolUse`
hooks are documented allow/deny/prompt only, explicitly with "no documented
mechanism to rewrite/modify tool input" and "no documented support" for
replacing a tool's result. Windows is fully supported here (OS-specific
hook commands) — the blocker is purely the missing modify/replace
capability, not platform support. Detects via `~/.copilot`.

#### `WindsurfAdapter` (`quor/adapters/windsurf_adapter.py`)

Structurally the closest of any QB-068/QB-069 tool to Claude Code's own
PreToolUse/Bash + PostToolUse/Read pair — Cascade has `pre_run_command`/
`post_run_command` and `pre_read_code`/`post_read_code`. Pre-hooks are
block-only (exit code 2, no structured modification response). Post-hooks
are the most directly confirmed "no" of any tool researched here — fetched
a second time, focused specifically on this question, and found: "Post-
hooks cannot block since the action has already occurred," documented
purpose is logging/tracking only, and hook stdout is only ever shown in the
UI, never fed back into what Cascade's model sees. Windows is fully,
explicitly supported (a dedicated `powershell` hook field). Detects via
`~/.codeium/windsurf`.

#### `AiderAdapter` (`quor/adapters/aider_adapter.py`)

The starkest case: Aider has no tool-call hook system at all, not even an
allow/deny-shaped one — its only configurable touch points
(`--lint-cmd`/`--test-cmd`) wrap Aider's own auto-lint/auto-test feature,
not a general interception mechanism. Detection differs from every other
adapter in this family: Aider is a plain CLI with no guaranteed
home-directory footprint, so `_detect()` checks three independent signals
(`aider` on `PATH`, project-local `.aider.conf.yml`, user-level
`.aider.conf.yml`) and reports whichever it found.

#### `ContinueAdapter` (`quor/adapters/continue_adapter.py`)

Same "no hook system at all" conclusion as Aider, independently reached:
fetching Continue's own `config.yaml` reference and enumerating every
documented top-level key (`name`, `version`, `schema`, `models`, `context`,
`rules`, `prompts`, `docs`, `mcpServers`, `data`) found no `hooks` key and
no mention of any lifecycle-hook mechanism anywhere. Continue's only
extension points — MCP servers and slash-command prompts — are both
agent-optional or text-only, neither able to intercept a tool call. Detects
via `~/.continue`.

## `quor doctor` / `quor init` behavior for new, optional adapters

Codex, Gemini, Cursor, VS Code, Windsurf, Aider, and Continue.dev are all
new integrations nobody has opted into by default. **Their absence must
never flip `quor doctor`'s overall pass/fail for a user who only uses
Claude Code** — that would be exactly the kind of existing-user regression
this task's own principles rule out. Every non-Claude adapter's "not
installed" checks are therefore advisory (`ok=True`) with an explanatory
detail, gated on an adapter-owned signal (e.g. Gemini's own hook script
path, or each `DetectionOnlyAdapter` subclass's own `_detect()`) rather
than on a shared file another adapter might have already created (see
`gemini_adapter.py::doctor_checks`'s own comment for why checking
`settings.json` existence alone would have been wrong — it can exist purely
because Claude Code was installed). Once a user actually runs
`quor init --agent gemini`, real pass/fail signal kicks in for that
adapter's own checks. Pure-logic roundtrip checks (does `handle_bytes()`
correctly rewrite a synthetic payload) always run and are always genuine
pass/fail, independent of install state — they verify Quor's own code, not
whether the user opted in. The six `DetectionOnlyAdapter` subclasses have
no such roundtrip check (there is no working hook logic to roundtrip) —
their two checks are both always advisory, by design (see
`quor/adapters/_detection_only.py::doctor_checks`).

## Adding a new adapter

1. **Research the target tool's actual hook/extension contract before
   writing any code** — do not assume it mirrors Claude Code's. Confirm:
   can the hook modify a tool call's input (required for
   `COMMAND_INTERCEPT`) or a tool's output (required for
   `CONTENT_INTERCEPT`)? What's the exact stdin/stdout JSON shape? Is the
   platform (Windows, since Quor is Windows-first) actually supported?
   Fetch the tool's own current documentation directly — six adapters in a
   row (QB-068/QB-069) found the answer differs from what a blog post or a
   sibling tool's shape would suggest.
2. **If the research confirms a real modify/replace capability** (like
   Gemini CLI's `BeforeTool.tool_input` merge): implement the `AgentAdapter`
   Protocol directly — see `quor/adapters/gemini_adapter.py` for the
   fullest worked example (own Pydantic payload models, `handle_bytes()` as
   a standalone bytes-in/bytes-out function, `install()`/`doctor_checks()`).
   **If it doesn't** (allow/deny-only, observational-only, or no hook
   system at all — the outcome for six of eight built-in adapters so far):
   subclass `DetectionOnlyAdapter` (`quor/adapters/_detection_only.py`)
   instead — supply `agent_id`/`display_name`/`limitation_reason` and a
   `_detect()` method; every other `AgentAdapter` method is already
   implemented. Do not write a new no-op `install()`/`doctor_checks()` by
   hand — that duplication is exactly what this base class exists to
   prevent. Either way, never guess a capability you haven't confirmed.
3. Register it — built-in: add to `_builtin_adapters()` in
   `quor/adapters/registry.py`. Third-party: declare a `quor.hook_adapter`
   entry point in your package's `pyproject.toml`:
   ```toml
   [project.entry-points."quor.hook_adapter"]
   my_agent = "my_package.adapter:MyAgentAdapter"
   ```
4. Add it to the shared conformance suite's parametrization
   (`tests/unit/test_agent_adapter_protocol.py::ALL_BUILTIN_ADAPTER_CLASSES`
   for a built-in, plus `DETECTION_ONLY_ADAPTER_CLASSES` too if it's built
   on that base) plus its own dedicated test file for adapter-specific
   behavior: a real adapter needs rewrite/no-op/install/doctor/BOM-handling
   coverage (see `test_gemini_adapter.py`); a `DetectionOnlyAdapter`
   subclass needs only its own `_detect()` logic covered (see
   `test_cursor_adapter.py`/`test_windsurf_adapter.py` for the ~30-line
   shape — the shared base's own behavior is already proven generically).
5. Nothing in `quor/rewrite/`, `quor/filters/`, `quor/pipeline/`, or
   `quor/tracking/` should need to change. If it does, something has leaked
   agent identity into the core — stop and reconsider the adapter boundary
   instead.

## Testing strategy

- **Protocol conformance** (`test_agent_adapter_protocol.py`) — one
  parametrized suite across every built-in adapter: satisfies the
  `AgentAdapter` Protocol, class attributes present, `supported_events` is
  a `frozenset[AgentEvent]`, `handle_event()` returns `None` for an
  unsupported event, `doctor_checks()`/`install()` return well-shaped
  results without raising, agent IDs are unique. This is the "avoid
  duplicated tests" half of the task — generic contract behavior is tested
  once, not once per adapter.
- **Byte-for-byte equivalence** (`test_claude_adapter_equivalence.py`) —
  ClaudeAdapter-specific: proves the QB-068 refactor changed nothing
  observable versus the pre-existing `run_hook()` functions.
- **Registry discovery** (`test_adapter_registry.py`) — built-in lookup,
  entry-point discovery (Protocol validation, `api_version` rejection,
  fail-open per broken entry, built-in-shadows-third-party), mirroring
  `test_plugin_loader.py`'s structure for the two existing entry-point
  groups.
- **Detection-only shared contract** (`test_agent_adapter_protocol.py::
  TestDetectionOnlyAdapterSharedContract`, parametrized across all six
  `DetectionOnlyAdapter` subclasses) plus `test_detection_only_adapter.py`
  (the base class in isolation, via a minimal test double) — this is the
  QB-069 half of "avoid duplicated tests": the shape six adapters share is
  tested once, not six times.
- **Adapter-specific behavior** — whatever is unique to that adapter:
  `test_gemini_adapter.py` covers Gemini's rewrite/BOM/install/doctor
  behavior in full (it has real logic to test); `test_codex_adapter.py`,
  `test_cursor_adapter.py`, `test_vscode_adapter.py`,
  `test_windsurf_adapter.py`, `test_aider_adapter.py`,
  `test_continue_adapter.py` each cover only their own `_detect()` logic
  (a handful of tests each — everything else is the shared contract above).
- **Live discovery fixture** (`tests/fixtures/test_adapter`, an installable
  `quor-test-adapter` package declaring a real `quor.hook_adapter` entry
  point) — proves discovery works against a real installed package, not
  only a monkeypatched `importlib.metadata.entry_points()`. Installed as a
  separate CI/dev-setup step, same convention as `tests/fixtures/test_plugin`
  — see CONTRIBUTING.md.

## What this document does not cover

- `quor explain` has no `CONTENT_INTERCEPT` equivalent (no way to ask "how
  would Quor compress a Read of this file") — a pre-existing gap, not
  introduced or resolved by QB-068.
- Compression logic itself — see `docs/final/PROJECT_BIBLE.md` and
  `docs/final/DECISIONS.md` for `ContentMask`/`FilterRegistry`/`Pipeline`,
  none of which this task touched.
