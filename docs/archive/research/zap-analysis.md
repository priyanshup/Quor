# Zap Deep-Analysis: All 20 Deliverables

> Complete analysis of the Zap codebase as the foundation for our Python Prompt Optimization Engine.
> Every claim here is grounded in source code that was read in full — not inferred from documentation.

---

## Deliverable 1: Functional Specification

### What Zap Is

Zap is a command-line proxy tool for AI coding assistants that intercepts shell commands at the hook level, rewrites them to run through Zap's filtered versions, and compresses the output before it reaches the AI's context window.

### The Core User Problem

AI coding assistants run inside a token-constrained context window. Every `git log`, `cargo test`, `docker ps`, or `cat big_file.rs` output gets injected verbatim into that context. This is wasteful: most command output contains noise (ANSI escape codes, redundant status lines, verbose headers, unchanged resource states, passing test output) and only a fraction of it constitutes signal the AI actually needs.

### What Zap Does — Functional View

**Hook Phase (Command Interception)**
- Registers itself as a Claude Code PreToolUse hook (and equivalent for other agents)
- When the AI issues a shell command, Zap intercepts the JSON message before execution
- Rewrites the command: `git status` → `zap git status`
- The AI then runs the rewritten command

**Filter Phase (Output Compression)**
- The rewritten command runs `zap git status`, which:
  1. Executes the real `git status`
  2. Applies a sequence of transformations to the output
  3. Writes the compressed output to stdout for the AI to consume
- Reported token reductions: 50–95% depending on command type

**Analytics Phase (Tracking)**
- Every filtered command's token count (raw vs. compressed) is tracked in SQLite
- `zap gain` shows cumulative savings since installation
- Analytics are scoped per project

**Developer Experience**
- Zero friction: install once, invisible during normal development
- `zap init --claude` to install the hook
- `zap gain` to view savings
- `zap verify` to run filter self-tests
- `zap trust` to approve project-local TOML filters

### Supported Tool Ecosystems

Git / GitHub CLI / GitLab CLI · Cargo/Rust · Node.js / npm / pnpm / npx · TypeScript · Python (pytest, mypy, ruff, pip/uv) · Go · Docker / docker compose · Kubernetes (kubectl) · Terraform · AWS CLI · PostgreSQL (psql) · Make · diff · find · grep / ripgrep · ls · tree · File reads (cat/head/tail)

---

## Deliverable 2: Product Analysis

### What Market Problem Zap Solves

AI coding assistants are power consumers of shell output. A single `cargo test --workspace` failure can produce 5,000+ tokens of output. Over the course of a session, token waste is a tax on:
1. **Cost**: Most Claude Code usage is paid per token
2. **Context quality**: More noise means the AI misses important signals buried earlier in context
3. **Latency**: Larger context = slower inference

Zap's unique insight: **the hook sits between the AI and the shell, not between the developer and the shell**. The developer never sees compressed output. Only the AI does.

### Product Positioning
- **Category**: Context window optimizer / AI assistant middleware
- **Distribution**: CLI tool, installed once per developer
- **Revenue model**: None (open source) — drives Claude Code adoption by making it cheaper and faster
- **Target user**: Developers already using AI coding assistants in terminal sessions

### Product Strengths

1. Completely transparent to developer workflow — they see the same output they always did
2. Zero configuration required (sensible defaults for 60+ common commands)
3. Cross-agent support broadens the addressable audience significantly
4. Analytics create sticky behavior through demonstrated ROI ("I saved 2.3M tokens this month")
5. TOML filter system enables community contribution without Rust knowledge
6. The trust system for project-local filters is well-designed

### Product Weaknesses

1. **No validation that compression helps**: Zap asserts compressed output is better for AI. There is no A/B test data in the repository proving this.
2. **Hook installation is fragile**: Different format per agent (10+), breaks when agents update their API
3. **Rust binary requires compilation** or pre-built binaries for distribution
4. **Internal naming inconsistency** (rtk vs zap) creates confusion for contributors
5. **No streaming support**: Filters apply only after the entire command completes
6. **No feedback loop**: When a filter becomes ineffective (CLI tool updated output format), there's no detection mechanism

### Competitive Landscape

No direct competitors at time of analysis. Adjacent tools:
- Claude Code's own context truncation (blunt, not command-aware)
- Manual `| head` / `| grep` pipes (requires developer intent and knowledge)
- Zap is the only tool purpose-built for this specific problem

---

## Deliverable 3: Technical Architecture

### Two-Layer Architecture

```
[AI Coding Assistant]
     │ sends command: "git status"
     ▼
[Claude Code Hook / PreToolUse]
     │ hook_cmd.rs: reads JSON stdin → rewrites command
     ▼
[Hook Output: "zap git status"]
     │
     ▼
[Zap Binary: main.rs dispatcher]
     │ matches "git" subcommand → zap_git_handler()
     │ OR
     │ run_fallback() → TOML filter registry lookup
     ▼
[Execution Layer]
     │ spawns real "git status", captures stdout+stderr
     ▼
[Filter Layer]
     │ Code filter (filter.rs) for read/cat operations
     │ TOML filter (toml_filter.rs) for command output
     ▼
[Compressed Output] → printed to stdout → consumed by AI
     │
     ▼
[Tracking Layer: tracking.rs]
     └ records (command, project_path, tokens_before, tokens_after, exec_time_ms) → SQLite
```

### Component Breakdown

**Discovery Layer** (`src/discover/`)
- `rules.rs`: Static rule table (`RULES: &[RtkRule]`) with regex patterns, savings estimates, per-subcommand overrides
- `registry.rs` (3,888 lines): `classify_command()` + `rewrite_command()` — the hook's core routing logic
- `lexer.rs`: Quote-aware shell tokenizer for compound command splitting and heredoc detection

**Hook Layer** (`src/hooks/`)
- `hook_cmd.rs` (992 lines): Per-agent hook processors (Claude Code, Cursor, Gemini, Copilot)
- `init.rs` (6,274 lines): Hook installation for 10+ agents (file patching, atomic writes, migration)

**Core Layer** (`src/core/`)
- `toml_filter.rs` (1,698 lines): 8-stage declarative filter pipeline (59+ built-in filters)
- `filter.rs` (551 lines): Code-level filter for file reads (language-aware comment stripping)
- `tracking.rs` (1,690 lines): SQLite analytics with GLOB-based project scoping
- `config.rs`: TOML config file loading (`~/.config/rtk/config.toml`)
- `runner.rs`: Subprocess execution with stdout/stderr capture

**Command Layer** (`src/cmds/`)
- Individual handlers: `git/git.rs`, `python/pytest_cmd.rs`, `node/tsc_cmd.rs`, etc.
- Each implements domain-specific compression logic in Rust code

**Analytics Layer** (`src/analytics/`)
- `gain.rs`: Token savings report
- `cc_economics.rs`: Claude Code cost analysis
- `ccusage.rs`: Claude Code usage integration
- `session_cmd.rs`: Per-session analytics

### Build System

- `build.rs`: Concatenates all `src/filters/*.toml` files into a single embedded blob at compile time
- Lazy static registry: filters compiled once at process start, reused per process
- Release profile: `opt-level=3`, LTO=true, 1 codegen unit, `panic=abort`, `strip=true` → <4MB binary

### Internal vs. External Naming

The codebase uses "rtk" (Rust Token Killer) as its internal name everywhere — config paths (`~/.config/rtk/`), env vars (`RTK_NO_TOML`, `RTK_HOOK_AUDIT`), error messages ("rtk: warning:"), test binary references. The public-facing name is "zap." This inconsistency is the result of an organic rename and is a contributor experience problem.

---

## Deliverable 4: Compression Pipeline Analysis

Zap has two distinct compression pipelines operating in different contexts.

### Pipeline A: TOML Filter Pipeline (Command Output)

Applied to the output of commands like `make`, `terraform plan`, `docker ps`, `cargo test`, etc.

**8 Stages (in strict execution order):**

**Stage 1: strip_ansi**
Removes ANSI escape codes (colors, cursor movement, clear-to-end-of-line sequences).
- Why: AI doesn't use color information; escape codes add 5–10% token overhead to colorized output
- Implementation: regex over each line (`\x1b[...m` patterns)

**Stage 2: replace**
Regex substitution rules, applied line-by-line, chainable.
- Supports backreferences (`$1`, `$2`)
- Multiple replace rules applied in sequence
- Why: More surgical than stripping entire lines; can normalize patterns (absolute paths → relative, timestamps → placeholder)

**Stage 3: match_output**
Short-circuit on blob match — if command output matches a pattern, replace the entire output with a short message.
- Optional `unless` guard: only apply if another pattern is NOT present in the output
- Why: If `cargo check` output contains only "Finished", no need to show 200 lines of compilation noise

**Stage 4: strip_lines_matching / keep_lines_matching** (mutually exclusive)
- `strip_lines_matching`: Drop lines matching any regex in the list
- `keep_lines_matching`: Keep ONLY lines matching any regex in the list
- Why: Removes known-noisy patterns (make entering/leaving dirs, terraform state refresh, docker pull layers)

**Stage 5: truncate_lines_at**
Truncate individual lines longer than N characters.
- Why: Long paths, SQL dumps, base64 blobs consume tokens without contributing meaning

**Stage 6: head_lines / tail_lines**
Keep first N and/or last M lines. Can combine both.
- Why: Most valuable signal is at the beginning (errors, what's being built) and end (summary, result)

**Stage 7: max_lines**
Absolute cap after all other stages.
- Why: Safety net for filters that pass more content than expected

**Post-pipeline:**
- `on_empty`: If output is empty after filtering, emit a custom message ("make: ok", "terraform plan: no changes detected"). Prevents AI from misinterpreting empty output as command failure.
- `filter_stderr`: If true, apply filter to stderr as well as stdout

### Pipeline B: Code Filter Pipeline (File Reads)

Applied when AI reads source files via `cat`/`head`/`tail` → `zap read`.

**FilterLevel Enum:**
- `None`: Raw file content, no transformation
- `Minimal`: Strip comments, normalize blank lines, preserve doc comments
- `Aggressive`: Structural extraction (signatures, imports, type definitions only)

**Language Detection (from file extension):**
- Code files: Rust, Go, Python, TypeScript/JavaScript, Java, C/C++, Ruby, etc.
- Data files: JSON, YAML, TOML, XML, CSV, Markdown, lock files → **NEVER code-filtered**

The data-file exclusion is critical. A previous bug caused JSON files with `packages/*` to be corrupted when `/*` was treated as a block comment start. Data files must be excluded entirely from code-level filtering.

**Minimal Filter:**
- Strips block/line comments (language-aware — different comment syntax per language)
- Normalizes multiple consecutive blank lines to single blank
- Preserves doc comments (`///`, `/**`, `#:`)
- Why: Comments are for humans; the AI reads the code structure directly

**Aggressive Filter:**
- Keeps: imports, function signatures, struct/class/enum/type declarations
- Strips: function bodies, method implementations
- `smart_truncate()`: Priority-based selection if output is still too large (imports and signatures get priority)
- Why: For large files where the AI only needs the API surface, not the full implementation

---

## Deliverable 5: Every Compression Rule Explained

Zap uses two types of rules: **Discovery Rules** (for command routing) and **TOML Filters** (for output compression).

### Discovery Rules (from `src/discover/rules.rs`)

There are 30+ rules, each specifying: regex pattern, target Zap command, rewrite prefixes, category, default savings estimate, and per-subcommand overrides.

| Command Pattern | → Zap Command | Category | Default Savings |
|---|---|---|---|
| `git\|yadm` + subcommand | `zap git` | Git | 70% (diff/show: 80%) |
| `gh pr\|issue\|run\|repo\|release` | `zap gh` | GitHub | 82% (pr: 87%) |
| `glab mr\|ci\|pipeline\|release` | `zap glab` | GitLab | 82% (mr: 87%) |
| `cargo build\|test\|clippy\|check\|fmt\|install` | `zap cargo` | Cargo | 80% (test: 90%) |
| `pnpm exec\|install\|list\|run` | `zap pnpm` | PackageManager | 80% |
| `npm exec\|run\|rum\|urn\|x` | `zap npm` | PackageManager | 70% |
| `npx ...` | `zap npx` | PackageManager | 70% |
| `cat\|head\|tail` | `zap read` | Files | 60% |
| `rg\|grep` | `zap grep` | Files | 75% |
| `ls` | `zap ls` | Files | 65% |
| `find` | `zap find` | Files | 70% |
| `tsc` (+ all npm/pnpm variants) | `zap tsc` | Build | 83% |
| `biome\|eslint\|lint` (+ npm/pnpm) | `zap lint` | Build | 80–87% |
| `next build` (+ npm/pnpm variants) | `zap next` | Build | 75% |
| `playwright test` (+ npm/pnpm) | `zap playwright` | Build | 85% |
| `pytest` + `python -m pytest` | `zap pytest` | Tests | 90% |
| `mypy` + `python3 -m mypy` | `zap mypy` | Build | 80% |
| `ruff check\|format` | `zap ruff` | Build | 75% |
| `pip\|pip3\|uv pip list` | `zap pip` | PackageManager | 70% |
| `go test\|build\|vet\|mod` | `zap go` | Build | 85% |
| `golangci-lint run` | `zap golangci-lint` | Build | 80% |
| `swift test\|build` | `zap swift` | Build | 90% |
| `docker ps\|run\|exec\|build` | `zap docker` | Infra | 85% |
| `docker compose ps\|logs\|build` | `zap docker compose` | Infra | 80% |
| `kubectl get\|describe\|apply\|logs` | `zap kubectl` | Infra | 85% |
| `terraform plan\|apply\|validate` | `zap terraform` | Infra | 80% |
| `aws ...` | `zap aws` | Network | 75% |
| `psql ...` | `zap psql` | Network | 70% |
| `diff ...` | `zap diff` | Files | 65% |
| `tree` | `zap tree` | Files | 70% |

### Special Routing Exceptions (from `src/discover/registry.rs`)

These are not in the rules table — they are hardcoded special cases:

- `head -N file` → `zap read file --max-lines N`
- `tail -N file` → `zap read file --tail-lines N`
- `head -N a b c` (multiple files) → **NOT rewritten** (multi-file with `==>` banners incompatible)
- `cat -A/-v/-e/-t/-s file` → **NOT rewritten** (flags have different semantics than `zap read`)
- `cat -n file` → `zap read -n file` (line numbers flag IS compatible)
- `gh --json/--jq/--template ...` → **NOT rewritten** (structured output would be corrupted)
- `find ... | xargs/wc` → **NOT rewritten** (output format incompatible with pipe consumers)
- `docker compose up/down/config` → **NOT rewritten** (interactive or structured output)

### TOML Filter Examples

**make.toml** — strips entering/leaving directory noise:
```toml
[filters.make]
match_command = "^make\\b"
strip_lines_matching = ["^make\\[\\d+\\]:", "^\\s*$", "^Nothing to be done"]
max_lines = 50
on_empty = "make: ok"
```

**terraform-plan.toml** — strips state refresh noise, keeps plan diff:
```toml
[filters.terraform-plan]
match_command = "^terraform\\s+plan"
strip_ansi = true
strip_lines_matching = [
  "^Refreshing state", "^\\s*#.*unchanged",
  "^\\s*$", "^Acquiring state lock", "^Releasing state lock"
]
max_lines = 80
on_empty = "terraform plan: no changes detected"
```

---

## Deliverable 6: Hook Architecture

Zap implements hooks at the **PreToolUse** level — the hook runs before the AI's command is executed, making it a pure read-and-rewrite operation with no side effects on its own.

### Hook Communication Protocol

All hooks communicate via **JSON over stdin/stdout**:
- AI coding assistant sends a JSON object to the hook's stdin
- Hook reads it, rewrites the command field, writes modified JSON to stdout
- AI reads the hook's stdout and uses the modified command

The hook is a child process. The AI waits for it to exit. The exit code and stdout are the hook's output.

### Per-Agent Hook Implementations

**Claude Code:**
```json
Input:  {"tool_input": {"command": "git status"}, "hookEventName": "PreToolUse"}
Output: {
  "hookEventName": "PreToolUse",
  "hookSpecificOutput": {"updatedInput": {"command": "zap git status"}},
  "permissionDecision": "allow",
  "permissionDecisionReason": "Zap auto-rewrite"
}
```

**Cursor (Windows-specific BOM handling):**
```
Input:  "\xEF\xBB\xBF\xEF\xBB\xBF{...}"   (doubled UTF-8 BOM)
Output: {"continue": true, "permission": "allow", "updated_input": {"command": "zap git status"}}
```
Cursor on Windows sends a doubled BOM (`EF BB BF EF BB BF`) before the JSON. Zap strips this before parsing. Without this fix, the JSON parse fails.

**Gemini:**
```json
Input:  {"tool_input": {"command": "..."}} from "run_shell_command" tool
Output: {"decision": "allow", "hookSpecificOutput": {"tool_input": {"command": "zap ..."}}}
```

**Copilot:**
- Auto-detects two formats: VS Code format (snake_case `tool_name`) vs. CLI format (camelCase `toolName` + JSON-encoded `toolArgs`)
- Different JSON structures for the same conceptual operation

### Safety Mechanisms in Hook Processing

**Heredoc detection (`has_heredoc()` in registry.rs):**
- Quote-aware lexer tokenizes the command
- Returns `true` if a `<<` redirect token appears outside of quotes
- If heredoc detected → hook returns `None` (no rewrite) → command executes unchanged
- Prevents corrupting multiline heredoc commands

**`RTK_DISABLED=1` prefix detection:**
- `RTK_DISABLED=1 git status` → hook detects this in env prefix via regex, skips rewrite entirely
- Also emits a warning to stderr: "RTK_DISABLED=1 detected — skipping filter for this command. Remove RTK_DISABLED=1 to restore token savings."
- This educates the AI to stop using this as an escape hatch

**Exclude list:**
- Config: `hooks.exclude_commands = ["curl", "playwright"]`
- Commands matching these patterns are never rewritten
- Patterns are compiled to regex at startup (not per-invocation)

**Transparent prefixes (recursive):**
- Config: `hooks.transparent_prefixes = ["docker exec mycontainer"]`
- `docker exec mycontainer git status` → strip prefix → classify `git status` → rewrite → prepend prefix back
- `docker exec mycontainer zap git status`
- Recursion depth limited to 10 (prevents infinite loop from circular configs)
- Longer prefixes matched first (sorted by length descending before matching)

**Compound command handling:**
```
"git add . && cargo test"
→ split on &&
→ rewrite "git add ." → "zap git add ."
→ rewrite "cargo test" → "zap cargo test"
→ "zap git add . && zap cargo test"
```
- `&&`, `||`, `;`, `&` operators all trigger compound splitting
- Pipe (`|`): only left-hand side rewritten; right side stays raw
- `$((` arithmetic expressions: NOT split (can contain `&&` inside)
- Redirect suffixes (`2>&1`, `&>/dev/null`): stripped before matching, re-appended after rewrite

**stdin size cap:** 1 MiB maximum to prevent memory exhaustion

**Audit logging:**
- `RTK_HOOK_AUDIT=1` env var enables pipe-delimited audit log to file
- Sanitizes newlines and pipe characters to prevent log injection

### Hook Installation

Implemented in `src/hooks/init.rs` (6,274 lines). Supports 10+ agents:

| Agent | Files Modified |
|---|---|
| Claude Code | `~/.claude/settings.json` + hook script |
| Cursor | `.cursorrules` or `cursor_rules/` directory |
| Windsurf | `.windsurfrules` |
| Cline | `.clinerules` |
| Kilocode | `.kilocode/rules/rtk-rules.md` |
| Antigravity | `.agents/rules/antigravity-rtk-rules.md` |
| Hermes | Python plugin + YAML config |
| OpenCode | TypeScript plugin |
| Pi | TypeScript extension |
| Codex | `AGENTS.md` |

**Installation safety features:**
- **Idempotency**: `hook_already_present()` checks for both legacy script path and new binary command format. Re-running `zap init` is safe.
- **Atomic writes**: `NamedTempFile` + `persist()` (rename). Prevents partial writes that could corrupt hook scripts.
- **Dry run mode**: `--dry-run` shows what would change without writing anything.
- **Migration**: Automatically removes old `rtk-rewrite.sh` shell script and its `settings.json` entry, installs new binary-based hook.
- **YAML format detection**: Hermes config uses both inline `[a, b]` and block `- a\n- b` YAML formats; both are handled.

---

## Deliverable 7: CLI Analysis

Zap exposes 50+ subcommands via Clap 4 (derive macro). Key categories:

### Hook Management
- `zap init --claude` — Install Claude Code hook (also: `--cursor`, `--gemini`, `--copilot`, `--windsurf`, `--cline`, `--all`)
- `zap hook claude` — Run the Claude Code hook (this is what the installed hook script calls)
- `zap hook cursor` / `zap hook gemini` / `zap hook copilot` — Per-agent hook runners
- `zap hook check` — Verify hook is correctly installed and functioning

### Core Command Filters (selected)
- `zap git [subcommand]` — Git with domain-specific output filtering
- `zap cargo [subcommand]` — Cargo with output filtering
- `zap pytest [args]` — pytest with failure-focused filtering
- `zap mypy [args]` — mypy with type error filtering
- `zap tsc [args]` — TypeScript compiler with error filtering
- `zap lint [args]` — ESLint/Biome with warning filtering
- `zap docker [subcommand]` — Docker with output filtering
- `zap kubectl [args]` — kubectl with resource filtering
- ...and 30+ more

### File Operations
- `zap read [file]` — Read file with code filter (language-aware comment stripping)
- `zap read [file] --max-lines N` — Read with line limit
- `zap read [file] --tail-lines N` — Read last N lines
- `zap grep [pattern] [path]` — Search with result capping
- `zap ls [path]` — Directory listing with noise filtering
- `zap find [args]` — Find with result limiting
- `zap tree [path]` — Directory tree with depth limiting

### Analytics
- `zap gain` — Show token savings report for current project
- `zap gain --json` — Machine-readable savings report
- `zap gain --project /path` — Scoped to a specific project path
- `zap gain --global` — Across all projects

### Discovery and Learning
- `zap discover` — Analyze shell history, find commands that could benefit from filtering
- `zap learn` — Track what commands the AI runs to identify new filter opportunities

### Configuration and Trust
- `zap config` — Show current configuration and file path
- `zap trust [path]` — Approve a project-local `.rtk/filters.toml` file (SHA-256 recorded)
- `zap verify` — Run all inline TOML filter tests
- `zap verify --filter make` — Run tests for a specific filter

### Diagnostic
- `zap rewrite [command]` — Show what a command would be rewritten to (debug mode)
- `zap classify [command]` — Show classification result with category and savings estimate
- `zap version` — Version information

### Routing Logic in main.rs

The `main.rs` dispatcher implements intelligent routing for npx/npm/pnpm invocations:
- `npx tsc` → routes to `zap tsc` handler (not generic `zap npx`)
- `npx eslint` → routes to `zap lint` handler
- `npx playwright` → routes to `zap playwright` handler
- `pnpm run tsc` → same as `npx tsc`
- Unknown `npx foo` → TOML filter registry lookup

`run_fallback()` handles TOML-only commands (commands that have no Rust handler):
- Looks up command in TOML filter registry
- Found: execute real command + apply TOML filter pipeline
- Not found: passthrough (execute normally + record as passthrough with 0/0 tokens)
- `RTK_NO_TOML=1` env var bypasses TOML engine entirely
- `RTK_TOML_DEBUG=1` enables debug output for filter engine

---

## Deliverable 8: Configuration Analysis

### Config File

Location: `~/.config/rtk/config.toml` (via `dirs::config_dir()`)

```toml
[tracking]
enabled = true            # Track token savings in SQLite
history_days = 90         # Auto-delete records older than this
# database_path = "..."   # Optional override for SQLite path

[display]
colors = true
emoji = true
max_width = 120

[filters]
ignore_dirs = [".git", "node_modules", "target", "__pycache__", ".venv", "vendor"]
ignore_files = ["*.lock", "*.min.js", "*.min.css"]

[hooks]
exclude_commands = []              # Commands to never rewrite (regex patterns)
transparent_prefixes = []          # Wrapper prefixes to strip before routing

[limits]
grep_max_results = 200             # Total grep results cap
grep_max_per_file = 25            # Per-file grep results cap
status_max_files = 15             # Max files shown in git status
status_max_untracked = 10        # Max untracked files in git status
passthrough_max_chars = 2000     # Output cap for passthrough (unfiltered) commands

[tee]
# Configuration for tee-ing output to external systems (logging, audit)

[telemetry]
enabled = false
consent_given = null
consent_date = null
```

**Config loading behavior:**
- If file doesn't exist: use defaults everywhere. No error.
- If file exists but section is missing: use defaults for that section. Backwards-compatible.
- If file has unknown keys: ignored (Serde default).
- Invalid TOML: returns error — the one case that fails loudly.

### TOML Filter Lookup Priority

Three tiers, applied in priority order:
1. `.rtk/filters.toml` in project root → requires `zap trust` approval + SHA-256 hash verification
2. `~/.config/rtk/filters.toml` → user-global custom filters, no approval required
3. Built-in filters embedded at compile time → 59+ filters for common tools

First match wins within each tier. Tier 1 overrides Tier 2 overrides Tier 3.

### Environment Variable Overrides (No Config File Needed)

| Variable | Effect |
|---|---|
| `RTK_NO_TOML=1` | Bypass TOML filter engine entirely |
| `RTK_TOML_DEBUG=1` | Enable debug output for filter pipeline |
| `RTK_HOOK_AUDIT=1` | Enable hook audit logging |
| `RTK_DB_PATH=/path/to.db` | Override SQLite database path |
| `RTK_DISABLED=1` (command prefix) | Skip rewrite for that specific command |

### Trust System Details

Project-local TOML filters must be explicitly trusted because they execute arbitrary regex against AI command output — a potential vector for filter poisoning.

```
zap trust .rtk/filters.toml
```
- Computes SHA-256 of the filter file
- Stores `{absolute_path: hash}` in a trust database
- On filter load: verify hash matches current file content
- Status: `Trusted` (hash matches) → apply filter
- Status: `ContentChanged` (hash mismatch) → **block filter**, warn user
- This prevents supply-chain attacks where a trusted filter is modified after approval

---

## Deliverable 9: Safety Mechanisms

Zap has multiple independent safety layers. This section covers each in detail, including the design rationale.

### Layer 1: Meaning Preservation (Output Safety)

**Core invariant**: Filtered output must contain all information the AI needs to make correct decisions. This is enforced through:

- **`on_empty` fallback**: If all output is stripped, emit a human-readable status message. Prevents AI from treating empty output as "command failed."
- **Conservative patterns**: `strip_lines_matching` patterns are anchored (`^`), not broad contains-matches. Strip only what is definitively noise.
- **`match_output` with `unless` guard**: Short-circuit only when pattern A is present AND guard pattern B is absent. Prevents false positives.
- **Data files never code-filtered**: JSON/YAML/TOML/Markdown are categorically excluded from the code filter pipeline. Any regex applied to these formats risks corrupting their syntax. (The `packages/*` JSON bug demonstrates exactly this failure mode.)
- **Aggressive filter is opt-in**: `zap read` defaults to `Minimal` filter level. `Aggressive` must be explicitly requested.

### Layer 2: Command Safety (Hook Safety)

**Heredoc protection** (`has_heredoc()` in `registry.rs`):
```rust
pub fn has_heredoc(cmd: &str) -> bool {
    tokenize(cmd)
        .iter()
        .any(|t| t.kind == TokenKind::Redirect && t.value.starts_with("<<"))
}
```
- Quote-aware lexer correctly identifies `<<` outside quotes as a heredoc
- `<<` inside quotes is string content, not a heredoc
- Commands containing heredocs bypass the hook entirely — no rewrite attempted

**Passthrough on no match**: If a command has no matching filter or rule, it runs normally. The hook never modifies a command it can't route correctly. Failure mode is "no compression" not "wrong command."

**Exclude list enforcement**: `exclude_commands` patterns are compiled to regex at startup. Commands matching any pattern are never rewritten, regardless of whether a filter exists.

**Compound command segment independence**: Each segment of a compound command is classified independently. If `git add .` is supported but `htop` is not, the result is `zap git add . && htop` — not either fully rewriting or fully skipping the chain.

**Redirect preservation**: `git status 2>&1` → trailing redirects are tokenized and stripped from the command before classification, then re-appended to the rewrite result: `zap git status 2>&1`.

**Pipe-consumer protection**: `find` in a pipe is explicitly detected and not rewritten:
```rust
let is_pipe_incompatible = seg.starts_with("find ") || seg == "find"
    || seg.starts_with("fd ") || seg == "fd";
```
Reason: `zap find` produces a formatted, summarized listing. `find | xargs` or `find | wc -l` requires raw find output.

**`gh --json` protection**: `gh` commands with `--json`, `--jq`, or `--template` flags are explicitly excluded from rewriting. These produce structured output the AI uses programmatically; compression would corrupt it.

**`cat` flag semantics**: Only `-n` (line numbers) maps correctly to `zap read -n`. All other `cat` flags (`-v`, `-A`, `-e`, `-t`, `-s`) have different semantics with no `zap read` equivalent. The rewrite is skipped.

### Layer 3: Project-Local Filter Security

**SHA-256 trust verification**: Project-local filters require explicit user approval via `zap trust`. The hash is recorded. If the file changes after trust, `ContentChanged` status blocks the filter from running. This prevents: (1) untrusted filters from running automatically, (2) approved filters from being modified silently.

**Atomic file writes**: All config file and hook script writes use `NamedTempFile::new()` + `.persist(destination)`. This is a create-in-temp-then-rename pattern — the destination is never partially written.

### Layer 4: Analytics Safety

**WAL mode + busy timeout**: `PRAGMA journal_mode=WAL` allows concurrent readers without blocking writers. `PRAGMA busy_timeout=5000` prevents immediate failure when two Zap processes race for the write lock.

**GLOB not LIKE for project scoping**:
```sql
WHERE project_path GLOB '/home/user/project/*'
```
LIKE uses `_` as a wildcard, which would accidentally match project paths containing underscores. GLOB uses `*` and `?` only, making the query precise.

**90-day auto-cleanup**: Executed on each record insert, not on a schedule:
```sql
DELETE FROM commands WHERE recorded_at < datetime('now', '-90 days')
```
This prevents unbounded database growth without requiring a background job.

### Layer 5: Input Safety

**1 MiB stdin cap**: Hook stdin is capped at 1 MiB. Prevents memory exhaustion if the AI sends a malformed or extremely large JSON message.

**Log injection prevention**: Audit log entries sanitize newlines (replace with `\n`) and pipe characters before writing to the pipe-delimited log file.

---

## Deliverable 10: Hidden Optimizations

These are non-obvious engineering choices discovered in the source code that don't appear in any documentation.

### 1. RegexSet for O(n)-ish Classification

```rust
lazy_static! {
    static ref REGEX_SET: RegexSet =
        RegexSet::new(RULES.iter().map(|r| r.pattern)).expect("invalid regex patterns");
    static ref COMPILED: Vec<Regex> = RULES.iter()
        .map(|r| Regex::new(r.pattern).expect("invalid regex")).collect();
}
```

`RegexSet` checks all 30+ patterns in a single pass over the input. Only after identifying which rules match does it use individual compiled regexes to extract capture groups. This is significantly faster than testing rules sequentially.

**Why this matters**: The hook runs in the critical path. `classify_command()` must complete in <1ms to stay transparent to the developer.

### 2. Last-Match Wins for Specificity

When multiple rules match the same command (e.g., `npm exec tsc` matches both the `npm exec|run` rule and the `tsc` rule), the **last match** from `REGEX_SET.matches()` is used:
```rust
let matches: Vec<usize> = REGEX_SET.matches(cmd_clean).into_iter().collect();
if let Some(&idx) = matches.last() {
```

Rules in `RULES` are ordered from general to specific. The most specific rule is at a higher index and wins automatically. This is achieved through careful rule ordering in the static table — no explicit priority field needed.

### 3. Build-Time TOML Concatenation

`build.rs` concatenates all `src/filters/*.toml` files into a single string, which is `include_str!`-embedded in the binary at compile time. The filter registry parses this once at process start via `lazy_static!`. There is no I/O, no disk access, and no startup overhead for the 59+ built-in filters.

This is the Rust equivalent of bundling assets — but implemented as a custom build script rather than a standard asset bundler.

### 4. Transparent Prefix Recursion (Depth-Limited)

The `rewrite_segment_inner()` function recurses:
1. Strip env prefix (`FOO=1 bar baz` → env_prefix=`FOO=1 `, rest=`bar baz`)
2. Strip shell builtins (`exec`, `builtin`, `noglob`, etc.)
3. Strip user-configured transparent prefixes (`docker exec mycontainer`)
4. Then classify the innermost command

Each recursion decrements `depth`. At `depth >= MAX_PREFIX_DEPTH (10)`, return `None`. This prevents pathological inputs from causing stack overflow or infinite loops.

### 5. Passthrough with Zero-Token Recording

When a command has no filter match, it executes normally. The tracking system records it with `tokens_before = 0` and `tokens_after = 0`:
```rust
pub fn track_passthrough(&mut self, db: &TrackingDb) {
    db.record_execution(self.command, self.project, 0, 0, self.elapsed_ms);
}
```

This preserves the complete execution log while preventing passthrough commands from diluting savings statistics. The analytics queries for `zap gain` explicitly exclude zero-token records.

### 6. Absolute Path Normalization Before Matching

```rust
fn strip_absolute_path(cmd: &str) -> String {
    // /usr/bin/grep -rn foo → grep -rn foo
    if first_word.contains('/') {
        let basename = first_word.rsplit('/').next().unwrap_or(first_word);
        ...
    }
}
```

The AI sometimes invokes binaries by absolute path (common in some shell environments). Without this normalization, `/usr/bin/grep -rn foo` would fail to match the `^(rg|grep)\s+` pattern. This was bug #485 — found in production.

### 7. Git Global Option Stripping + Preservation

`git -C /tmp status` is classified as `git status` (for matching) but rewritten to `zap git -C /tmp status` (preserving the option). Two separate passes:
1. Strip global opts (`-C path`, `--git-dir`, `--work-tree`) before regex matching
2. Use the original command string (not the stripped version) as the basis for rewriting, but prefix it correctly

This required recognizing that classification and rewriting need different views of the command.

### 8. Token Estimation Deliberately Approximate

`ceil(chars / 4.0)` — The actual Claude tokenizer uses Byte Pair Encoding. The actual ratio varies: English prose is ~4 chars/token; code with long identifiers is ~3–5 chars/token; Asian languages are ~1–2 chars/token. Zap uses a fixed ratio that slightly overestimates for ASCII code.

This is intentional: the goal is directional savings metrics, not billing accuracy. Using the real tokenizer would add a network call or a large dependency and 10–100ms latency per command. For "we saved ~40%" purposes, the approximation is sufficient.

### 9. `$((` Protection in Compound Splitting

```rust
if has_heredoc(trimmed) || trimmed.contains("$((") {
    return vec![trimmed];
}
```

Arithmetic expansion `$((a && b))` contains `&&` which would normally trigger compound command splitting. But splitting on `&&` inside `$((` would produce `cmd $((a ` and ` b))`, neither of which is a valid command. The check is string-based (not lexer-based) as an intentionally conservative guard.

### 10. find + pipe Detection Prevents Format Mismatch

```rust
let is_pipe_incompatible = seg.starts_with("find ") || seg == "find"
    || seg.starts_with("fd ") || seg == "fd";
let rewritten = if is_pipe_incompatible {
    seg.to_string()    // Don't rewrite
} else {
    rewrite_segment(seg, excluded, transparent_prefixes)
        .unwrap_or_else(|| seg.to_string())
};
```

`zap find` produces a line-truncated, path-formatted summary. When `find` output is piped to `xargs` or `wc -l`, the consumer expects raw paths, one per line. Rewriting `find` in a pipe would silently corrupt the pipeline's semantics.

---

## Deliverable 11: Performance Analysis

### Reported Specifications (from README)
- Binary size: < 4MB
- Cold start: < 10ms
- Memory: < 5MB per process
- Filter overhead: 2–15ms per command

### Binary Size

The release profile achieves < 4MB through:
- `opt-level = 3` — maximum optimization
- `lto = true` — link-time optimization removes unused code
- `codegen-units = 1` — enables maximum LTO effectiveness
- `panic = "abort"` — removes Rust's panic unwinding machinery (~100KB savings)
- `strip = true` — removes debug symbols from binary

The `rusqlite` dependency is compiled in with `features = ["bundled"]`, which includes SQLite source. This adds ~600KB but eliminates the requirement for a system SQLite installation.

### Startup Time

< 10ms cold start is achievable because:
- Lazy statics are initialized on first use, not at startup
- No network calls
- Config file read is a single `fs::read_to_string` (~0.5ms for a small file)
- SQLite open in WAL mode: ~2–3ms

**The critical insight about lazy static**: Because Claude Code caches the hook binary in memory, the "cold start" that matters is the second invocation onward. The first call to `REGEX_SET` compiles all 30+ regex patterns; subsequent calls are instant cache hits.

### Filter Overhead (2–15ms range)

- 2ms: Simple `strip_lines_matching` with 2–3 patterns on a 100-line output
- 15ms: Multi-stage regex-heavy filter (`replace` with complex patterns + `strip_lines_matching` + `keep_lines_matching`) on a 5,000-line output

The regex engine is Rust's `regex` crate, which uses DFA-based matching with bounded memory guarantees. It's fast but not free — complex patterns on large outputs are the upper bound.

### SQLite Performance

- WAL mode: concurrent readers don't block writers; multiple Claude Code tabs work without contention
- `busy_timeout = 5000`: 5-second grace period before failing on lock contention
- 90-day cleanup on insert: O(1) amortized (most deletes touch zero rows; cleanup only fires when old records exist)
- GLOB indexing: `CREATE INDEX idx_project ON commands(project_path)` — project-scoped queries use the index

### Real-World Bottleneck Analysis

The subprocess itself dominates: `git log -100` takes 50–200ms; the Zap overhead is 2–15ms. Net overhead is <10%. The tool is transparent in practice.

The actual risk to performance is **large failing test outputs**: a `pytest` run with 200 test failures produces thousands of lines. The filter still applies in linear time, but at the upper end of the 15ms estimate.

---

## Deliverable 12: Repository Architecture

```
zap/
├── Cargo.toml           — Dependencies, release profile (opt-level=3, LTO, strip)
├── Cargo.lock           — Pinned dependency versions
├── build.rs             — TOML concatenation at compile time (59+ filters → 1 embedded blob)
├── README.md            — Product documentation (12 filtering strategies, performance specs)
├── LICENSE              — MIT
├── src/
│   ├── main.rs          — 3,221 lines: Clap router, 50+ subcommands, run_fallback()
│   ├── lib.rs           — Library exports (for integration testing)
│   ├── core/
│   │   ├── config.rs    — Config struct (7 sections: tracking, display, filters, tee, telemetry, hooks, limits)
│   │   ├── constants.rs — String constants (RTK_DATA_DIR, CONFIG_TOML, DEFAULT_HISTORY_DAYS)
│   │   ├── filter.rs    — 551 lines: Code filter (language detection, Minimal/Aggressive levels)
│   │   ├── runner.rs    — Subprocess execution, stdout/stderr capture
│   │   ├── stream.rs    — Streaming output utilities
│   │   ├── tee.rs       — Output tee-ing to external systems
│   │   ├── telemetry.rs — Opt-in telemetry (disabled by default)
│   │   ├── toml_filter.rs — 1,698 lines: 8-stage TOML filter pipeline, trust system, inline tests
│   │   ├── tracking.rs  — 1,690 lines: SQLite analytics, GLOB scoping, WAL mode
│   │   ├── truncate.rs  — Smart truncation utilities
│   │   └── utils.rs     — Misc helpers
│   ├── discover/
│   │   ├── lexer.rs     — Quote-aware shell tokenizer (handles quotes, heredocs, operators)
│   │   ├── registry.rs  — 3,888 lines: classify_command(), rewrite_command(), has_heredoc()
│   │   ├── provider.rs  — Shell history provider (bash, zsh, fish history files)
│   │   ├── report.rs    — RtkStatus enum (Existing, Passthrough, Planned), discovery report
│   │   └── rules.rs     — Static RULES table: 30+ RtkRule structs with regex + metadata
│   ├── hooks/
│   │   ├── hook_cmd.rs  — 992 lines: Claude/Cursor/Gemini/Copilot hook processors
│   │   └── init.rs      — 6,274 lines: Hook installation for 10+ agents, atomic writes, migration
│   ├── analytics/
│   │   ├── gain.rs      — zap gain report (cumulative token savings)
│   │   ├── cc_economics.rs — Claude Code cost economics analysis
│   │   ├── ccusage.rs   — Claude Code usage data integration
│   │   └── session_cmd.rs — Per-session analytics
│   ├── learn/
│   │   ├── detector.rs  — CLI correction pattern detection
│   │   └── report.rs    — Learning report formatting
│   ├── cmds/            — Individual command handlers (30+ files)
│   │   ├── git/git.rs
│   │   ├── python/pytest_cmd.rs
│   │   ├── python/mypy_cmd.rs
│   │   ├── node/tsc_cmd.rs
│   │   ├── node/lint_cmd.rs
│   │   ├── infra/docker_cmd.rs
│   │   ├── infra/kubectl_cmd.rs
│   │   └── ...
│   └── filters/         — 59+ built-in TOML filter files
│       ├── make.toml
│       ├── terraform-plan.toml
│       ├── cargo.toml
│       ├── docker-ps.toml
│       └── ...
├── hooks/
│   └── claude/
│       ├── rtk-awareness.md  — 10-line "slim" awareness doc for Claude.md injection
│       └── rtk-rewrite.sh    — Legacy shell hook (pre-binary approach, kept for migration)
└── tests/
    └── fixtures/             — Test fixture files
```

### Architectural Patterns and Their Implications

**God router (`main.rs` at 3,221 lines)**: Makes entry points easy to find but makes individual commands hard to test in isolation. Each subcommand handler is a match arm in a giant `match commands { ... }` block.

**Two-track filter system**: Rust command handlers (`src/cmds/`) implement domain-specific logic. TOML filters (`src/filters/`) implement declarative rules. These are not unified under a common abstraction. The distinction is based on whether the command needs custom logic (git, pytest) or just pattern matching (make, terraform).

**Growing monolith in `init.rs`**: At 6,274 lines, this file contains all hook installation logic for 10+ agents. The cost of adding a new agent is adding another ~300-line function to an already huge file.

**Test strategy**: Inline tests in TOML files (`[[tests.make]]`), unit tests in Rust source files (`#[cfg(test)] mod tests`). No end-to-end tests visible. The registry test suite in `registry.rs` alone has 80+ test cases covering edge cases.

---

## Deliverable 13: Limitations

### Technical Limitations

**1. Single-process, complete-output model**
Zap captures stdout (and optionally stderr) from a completed subprocess. It cannot filter:
- Interactive output (ncurses, progress bars, interactive prompts)
- Streaming output (real-time build logs appearing line by line)
- Output from commands that write directly to the terminal file descriptor (bypassing stdout pipe)
- Distributed output from tools that spawn multiple processes

**2. Token estimation is approximate**
`ceil(chars / 4)` overestimates for dense code, underestimates for Unicode-heavy content. Savings reports show directional accuracy but not billing-level precision.

**3. Shell complexity limits**
The quote-aware lexer handles the common cases but cannot handle:
- Process substitution: `diff <(git show HEAD:file) file`
- Complex parameter expansion: `${var:-default}`
- Nested subshells with operators: `$(cmd1 && cmd2)`
- Here-strings: `cmd <<< "text"` (different from heredoc `<<`)
- Arithmetic expansion with logical operators: `$((a && b))` (handled with a string check, not proper parsing)

**4. Unix-only design**
Patterns and filter rules assume Unix paths, Unix command names, and Unix shell syntax. Windows paths, PowerShell syntax, and `cmd.exe` conventions are not addressed. (This is a significant limitation given some developers use Windows.)

**5. Filter quality is uneven**
High-quality filters exist for: git, cargo, pytest, make, terraform. Less certain coverage for: AWS CLI (output varies enormously by service/API version), psql (varies by query), kubectl (varies by resource type). Historical approach has been to add filters reactively after users report noisy output.

**6. No streaming filter**
Filters apply only after the command completes. For a 45-second `cargo build --workspace`, the AI waits 45 seconds then receives filtered output. No incremental filtering is possible.

**7. Filter format mismatch risk**
When a CLI tool updates its output format, TOML filters can silently become ineffective. The inline tests (`[[tests.filter]]`) test against hardcoded fixtures — they won't catch format changes in new tool versions.

### Product Limitations

**8. No empirical validation**
Zap asserts that compressed output improves AI task quality. This is a hypothesis. No A/B testing data, no before/after task completion rate comparison. The product's core value claim is unverified.

**9. No semantic understanding**
Zap uses regex patterns. It cannot distinguish "this error message is about the code change the AI is making" from "this warning is about an unrelated dependency." A rule that strips "warning:" lines could strip a critical warning relevant to the current task.

**10. Agent API volatility**
10+ AI coding assistants, each with a custom hook protocol that can change without notice. Every agent API update is a potential hook breakage. The 6,274-line `init.rs` must be updated manually for each agent change.

**11. No feedback loop for filter effectiveness**
If the AI re-runs a command after seeing compressed output (because it needed more detail), Zap has no way to detect this and automatically loosen the filter. Filter quality is static once deployed.

---

## Deliverable 14: Improvement Opportunities

### High Impact

**1. Semantic summarization stage**
Add an optional TOML filter stage: `summarize: true`. For commands where regex cannot achieve good compression (long test failure traces, complex build errors with context), call a fast AI model (Claude Haiku, local Ollama) to produce a 3–5 sentence summary. This is the capability that regex will never provide. The infrastructure is already in place (hooks can make network calls); it just needs to be wired into the filter pipeline.

**2. Compression quality feedback loop**
After each filtered command, observe whether the AI re-ran it or asked for more detail. High re-run rate = filter is too aggressive. Track this in SQLite. Surface it in `zap gain` as a "filter quality score." This data would allow automatic filter adjustment and would validate the core product hypothesis.

**3. Streaming filter mode**
For long-running commands (>10 second builds), apply filters incrementally as output arrives. Show the AI partial results ("15 errors so far, still running..."). Requires architectural work: the current `runner.rs` collects all output before filtering.

**4. Cross-agent test suite**
Zap supports 10+ agents but each hook implementation has its own test coverage. A single test that sends sample JSON through all 10 agent hook formats and verifies the output would prevent regressions when hook formats change.

### Medium Impact

**5. Data-driven `init.rs`**
Replace 6,274 lines of bespoke installation code with an agent manifest format:
```toml
[agents.windsurf]
config_file = ".windsurfrules"
hook_format = "markdown_block"
awareness_section = "## Zap\nZap rewrites commands..."
```
This would allow adding new agents without writing Rust code, and would make the installation behavior auditable without reading Rust.

**6. Filter preview command**
`zap preview <command>` — runs the command, shows: (A) raw output line count, (B) filtered output, (C) diff between them, (D) token savings. Makes it possible to understand and tune filters interactively without modifying TOML files.

**7. Community filter registry**
Enable `zap search helm` to find community-contributed filters, `zap install helm-filter` to add them. Currently filters are only built-in or project-local. A community registry (like a lightweight package index of TOML files) would expand coverage without growing the binary.

**8. Filter health monitoring**
`zap health` report: which filters have the most usage, which filters have the lowest savings ratio, which filters haven't been effective recently. This operational visibility would help maintainers prioritize filter improvements.

### Low Impact But High Quality

**9. Resolve the rtk/zap naming inconsistency**
Pick one name and use it everywhere. Config path: `~/.config/zap/`. Env vars: `ZAP_NO_TOML`, `ZAP_HOOK_AUDIT`. Error messages: "zap: warning:". Plan for a major version bump to avoid breaking existing installations. The internal inconsistency is a contributor experience problem.

**10. TOML filter schema versioning**
Add a `version` field to filter files. When built-in filters are updated, project-local filters that extend them can be validated against the new schema. Currently no versioning exists, so filter incompatibilities are silent.

---

## Deliverable 15: Product Critique

This is an honest assessment of Zap as a product and codebase — not a praise document.

### What Zap Gets Right

**The core insight is correct and valuable.** Intercepting at the PreToolUse hook level is the right place to compress. The AI gets filtered output; the developer sees nothing different. The transparency is genuine. This is the kind of insight that comes from someone who actually uses AI coding assistants daily.

**The TOML filter system is the product's best design decision.** Declarative, self-testing (`[[tests.make]]`), three-level priority (project > user > built-in), trust-gated for security. It solves the right problem: enabling community contribution without requiring Rust knowledge. The 8-stage pipeline is expressive enough to handle most real-world cases.

**The edge case handling is thorough.** The 80+ test cases in `registry.rs` — heredoc detection, pipe-incompatible `find`, `cat` flag semantics, `gh --json` corruption prevention, multi-file `head` with banners, `$((` arithmetic, BOM stripping, redirect preservation — these are bugs that were found in production and fixed. The codebase shows evidence of real-world battle-testing.

**Analytics as a retention mechanism is smart product design.** `zap gain` creates an anchor that makes the value tangible. "I saved 2.3M tokens this month" is a number people remember and share.

### Where Zap Falls Short

**The core value claim is unverified.** Zap asserts that compressed output improves AI task quality. This is a hypothesis, not a finding. It's plausible — AI models are sensitive to context length — but "less noise" could also mean "less context the AI needed." Without A/B testing, Zap is optimizing for token reduction, which may or may not correlate with task success rate. This is the most important gap in the product.

**`init.rs` is a maintenance liability.** 6,274 lines of bespoke installation logic for 10+ agents, each with idiosyncratic formats (YAML, Markdown, TypeScript plugin, JSON). Every agent update breaks a different function. The file will only grow. This should be data-driven (agent configuration as a manifest) rather than code-driven.

**The two-filter-system creates contributor confusion.** If you want to add filtering for `helm`, do you write a Rust handler in `src/cmds/` or a TOML filter in `src/filters/`? The answer is "TOML, unless you need custom logic," but this isn't documented. New contributors face this choice without guidance.

**`main.rs` at 3,221 lines signals this was never designed for external contributors.** The monolithic router is a single entry point, which is fine for a solo project. For an open-source project inviting contribution, it's a barrier. Each command should be independently understandable.

**The rtk/zap naming is unprofessional.** Config paths say `~/.config/rtk/`. Error messages say "rtk: warning." Env vars are `RTK_NO_TOML`. Users see "zap" and then find "rtk" everywhere internally. This is a direct result of an organic rename and tells contributors "this codebase wasn't designed, it evolved." For an open-source project seeking adoption, first impressions matter.

**No streaming means silence during long builds.** The AI sends `cargo test --workspace`, waits 45 seconds, and receives filtered output. During those 45 seconds, from the AI's perspective: nothing happened. No partial results, no progress indication. This is a UX problem that Zap's current architecture can't address without significant work.

---

## Deliverable 16: Suggested Python Architecture

Architecture for our Python Prompt Optimization Engine — informed by Zap but designed from first principles for Python, open source adoption, and long-term maintainability.

### Core Design Principles

1. **One abstraction for filters, not two.** Zap has Rust code handlers AND TOML filters — two different systems, no unified interface. We have one thing: filter plugins. TOML defines declarative behavior; Python defines logic when TOML isn't enough. Both are plugins.

2. **Plugin-first from day one.** Zap retrofitted TOML extensibility. We design the plugin system as the primary contribution path — even the built-in filters are just bundled plugins.

3. **Adapter pattern for AI agents.** Each AI agent is an adapter behind a clean interface. Adding a new agent means implementing one class, not adding 300 lines to a 6,274-line file.

4. **Observable by default.** Every filter records input size, output size, processing time, and stage trace. `gain` and `health` commands are first-class, not afterthoughts.

5. **Testable in isolation.** Every filter can be tested with `distill verify pytest` — no subprocess, no hook, no agent required. The filter pipeline is a pure function over strings.

6. **Windows from day one.** The user's corporate environment is Windows. Cross-platform paths, PowerShell-compatible hook scripts, and `platformdirs` for correct Windows config paths are requirements, not afterthoughts.

### Recommended Package Name

**Distill** — the act of purifying by removing what's unnecessary. Clean, professional, memorable, and available on PyPI. Uses "distill" everywhere with no internal alias.

### Package Structure

```
distill/                          — Repository root
├── pyproject.toml                — Package metadata, dependencies, plugin entry points
├── distill/
│   ├── __init__.py
│   ├── cli/                      — CLI layer (Typer recommended)
│   │   ├── main.py               — Root command group + app object
│   │   ├── hook.py               — `distill hook <agent>` commands
│   │   ├── gain.py               — `distill gain` analytics
│   │   ├── verify.py             — `distill verify` filter tests
│   │   ├── config.py             — `distill config` show/set
│   │   ├── trust.py              — `distill trust <path>`
│   │   └── preview.py            — `distill preview <command>` (Zap doesn't have this)
│   ├── adapters/                 — AI agent adapters
│   │   ├── base.py               — HookAdapter Protocol
│   │   ├── claude.py             — Claude Code adapter
│   │   ├── cursor.py             — Cursor adapter (BOM stripping)
│   │   ├── gemini.py             — Gemini adapter
│   │   └── copilot.py            — Copilot adapter
│   ├── rewrite/                  — Command routing / rewriting
│   │   ├── classifier.py         — classify_command(), rewrite_command()
│   │   ├── rules.py              — Rule dataclasses + RULES list
│   │   ├── lexer.py              — Quote-aware shell tokenizer
│   │   └── registry.py           — Compiled rule registry with LRU cache
│   ├── filters/                  — Filter system
│   │   ├── base.py               — FilterDef, FilterResult, FilterPlugin Protocol
│   │   ├── engine.py             — 8-stage pipeline runner
│   │   ├── toml_loader.py        — TOML filter loader (3-tier: project > user > built-in)
│   │   ├── trust.py              — SHA-256 trust system for project-local filters
│   │   └── builtin/              — Built-in TOML filters (20+ at launch)
│   │       ├── make.toml
│   │       ├── terraform-plan.toml
│   │       ├── pytest.toml
│   │       └── ...
│   ├── runner/                   — Subprocess execution
│   │   ├── executor.py           — Run commands, capture stdout/stderr
│   │   └── stream.py             — Streaming output (future)
│   ├── tracking/                 — SQLite analytics
│   │   ├── db.py                 — Connection management, WAL mode, busy timeout
│   │   ├── schema.py             — Table definitions, migration
│   │   └── report.py             — Analytics queries (gain, health, top commands)
│   ├── install/                  — Hook installation
│   │   ├── base.py               — AgentInstaller Protocol
│   │   ├── claude.py             — Claude Code installer
│   │   ├── cursor.py             — Cursor installer
│   │   └── manifest.py           — Agent manifest loader (data-driven, not code-driven)
│   └── config/                   — Configuration
│       ├── model.py              — Config dataclass
│       └── loader.py             — TOML config loading + env var overrides
├── tests/
│   ├── test_classifier.py        — 100+ test cases (one per rule + edge cases)
│   ├── test_engine.py            — Filter pipeline stage-by-stage tests
│   ├── test_adapters.py          — JSON round-trip tests for each agent
│   ├── test_trust.py             — Trust system tests
│   └── fixtures/                 — Test fixture files (sample command outputs)
└── plugins/                      — Example third-party plugin (template)
    └── distill-helm/
        ├── pyproject.toml
        └── distill_helm/filters/helm.toml
```

### Key Interface Definitions

```python
# distill/adapters/base.py
from dataclasses import dataclass
from typing import Protocol

@dataclass
class HookInput:
    command: str
    raw_json: dict
    agent: str

@dataclass
class HookOutput:
    rewritten_command: str
    original_input: HookInput
    allow: bool = True

class HookAdapter(Protocol):
    agent_name: str
    def parse_input(self, raw: bytes) -> HookInput: ...
    def format_output(self, output: HookOutput) -> bytes: ...
    def detect(self, raw: bytes) -> bool: ...
```

```python
# distill/filters/base.py
from dataclasses import dataclass, field
from typing import Optional, Protocol
import re

@dataclass
class FilterDef:
    name: str
    description: str
    match_command: re.Pattern
    strip_ansi: bool = False
    replace: list[dict] = field(default_factory=list)
    match_output: list[dict] = field(default_factory=list)
    strip_lines_matching: list[str] = field(default_factory=list)
    keep_lines_matching: list[str] = field(default_factory=list)
    truncate_lines_at: Optional[int] = None
    head_lines: Optional[int] = None
    tail_lines: Optional[int] = None
    max_lines: Optional[int] = None
    on_empty: Optional[str] = None
    filter_stderr: bool = False
    tests: list[dict] = field(default_factory=list)

@dataclass
class FilterResult:
    output: str
    original_lines: int
    filtered_lines: int
    tokens_before: int
    tokens_after: int
    stages_applied: list[str]
    truncated: bool = False

class FilterPlugin(Protocol):
    """Protocol for Python-code filter plugins (beyond TOML capability)."""
    name: str
    match_command: re.Pattern
    def filter(self, output: str, command: str, stderr: str = "") -> FilterResult: ...
    def test(self) -> list[tuple[str, bool, str]]: ...  # (test_name, passed, message)
```

```python
# distill/rewrite/rules.py
from dataclasses import dataclass, field

@dataclass(frozen=True)
class Rule:
    pattern: str
    target_command: str          # "distill git"
    rewrite_prefixes: list[str]  # ["git", "yadm"]
    category: str
    savings_pct: float
    subcmd_savings: dict[str, float] = field(default_factory=dict)
```

### Plugin System Design

Third-party plugins extend Distill without modifying core code:

```toml
# Plugin's pyproject.toml
[project.entry-points."distill.filters"]
helm = "distill_helm:HelmFilter"        # Python plugin

[project.entry-points."distill.toml_filters"]
helm = "distill_helm:get_filter_paths"  # TOML-only plugin
```

On startup, Distill discovers all entry points in both namespaces and registers the plugins. A `distill search helm` command would query PyPI for packages with `distill-` prefix to find community plugins.

This enables: `pip install distill-helm` → automatic Helm filtering support. No Rust knowledge required. TOML-only plugins can be authored and distributed entirely in Python package format.

### Why Not Just Port Zap's Architecture?

| Zap | Our Python | Reason |
|---|---|---|
| `main.rs` (3,221 lines) | `cli/` package with one module per command | Discoverability and contributor experience |
| Rust handlers + TOML (two systems) | Plugins (one protocol) | Single contribution path |
| `init.rs` (6,274 lines) | Data-driven manifest + `AgentInstaller` protocol | Each agent is addable without touching core |
| `lazy_static!` | `functools.lru_cache` + module-level compile | Python's natural equivalent |
| Build-time TOML concatenation | `importlib.resources` (stdlib) | No build step required |
| `~/.config/rtk/` | `~/.config/distill/` (via `platformdirs`) | Consistent, no internal alias |
| No streaming | Designed for streaming (even if not implemented in v1) | Architecture doesn't close the door |

---

## Deliverable 17: MVP Scope

### MVP Goal

Prove the core value proposition with minimal scope: **hook intercepts AI command → filter reduces output → AI context improves**. Nothing else matters until this works.

### MVP = Three Capabilities

**Capability 1: Claude Code Hook**
- Read JSON from stdin
- Parse `tool_input.command`
- Apply rewrite rules (classify + rewrite)
- Output modified JSON with `permissionDecision: "allow"`
- Handle: compound commands (`&&`), heredocs (no rewrite), env prefixes

**Capability 2: Command Filters (five commands)**

Prioritized by: developer frequency × token waste:
1. `git status` → strip untracked noise, cap at 20 lines
2. `git log` → strip author/date/hash, keep subject lines only, cap at 30
3. `git diff` → strip context lines, keep only changed lines + headers, cap at 100
4. `pytest` → strip passing tests (`PASSED`), keep only failures + summary line
5. `cat/read` → apply minimal code filter (strip comments, normalize blanks)

**Capability 3: Token Tracking**
- SQLite tracking: command, project_path, tokens_before, tokens_after
- `distill gain` showing cumulative savings

### MVP Exclusions (Explicitly Not In Scope)

- No Cursor/Gemini/Copilot (one agent only)
- No TOML filter system (hardcoded Python filter logic in MVP)
- No project-local filters or trust system
- No `discover` or `learn` commands
- No telemetry
- No streaming
- No plugin system
- No configuration file (environment variables only)
- No `verify` command
- No filter preview

### MVP Success Criteria

A developer installs the hook, runs 10 AI coding sessions, and:
1. `distill gain` shows ≥30% token reduction on filtered commands
2. Zero instances of the AI asking to re-run a filtered command to see more detail
3. Installation completes in under 2 minutes

### MVP Delivery Estimate

**Week 1**: Hook adapter + command classifier + `rewrite_command()` with compound command handling

**Week 2**: Five filter implementations + SQLite tracking + `distill gain` + `distill init --claude`

---

## Deliverable 18: Long-Term Roadmap

### Phase 0: MVP (Weeks 1–2)
Five filters, one agent, basic tracking. See Deliverable 17.

### Phase 1: Core Parity with Zap (Months 1–3)
- Full TOML filter engine (all 8 pipeline stages + inline tests)
- 20+ built-in filters (git, cargo, pytest, mypy, tsc, docker, kubectl, terraform, make, pip, ruff, go)
- Multi-agent support: Claude Code, Cursor, Gemini
- Project-local filters with SHA-256 trust
- Config file (`~/.config/distill/config.toml`)
- `distill verify [filter]` — run inline TOML tests
- `distill gain --project /path` — project-scoped analytics
- Windows/PowerShell hook script compatibility

### Phase 2: Beyond Zap (Months 3–6)
- **Plugin system**: `pip install distill-helm` adds Helm filter; entry-point discovery
- **`distill preview <command>`**: side-by-side raw vs. filtered output with diff
- **`distill health`**: which filters are effective, which are underperforming
- **`distill trust`**: improved UX — show what changed, why re-trust is needed
- **Multi-file `read`**: `distill read src/*.py --aggressive` for reading directories
- **Filter effectiveness tracking**: record whether AI re-ran commands after filtering

### Phase 3: Intelligence Layer (Months 6–12)
- **Semantic summarization**: Optional `summarize: true` stage in TOML; calls a fast AI model (Claude Haiku / local Ollama) to summarize what regex can't
- **Adaptive filters**: When re-run rate is high, automatically increase `max_lines` for that filter + that project
- **Community registry**: `distill search <keyword>` queries a registry of community TOML filter packages
- **Streaming filter mode**: For long-running builds, filter output incrementally
- **A/B testing infrastructure**: Measure whether filtering actually improves AI task completion rate

### Phase 4: Platform (Year 2)
- **IDE integration**: VS Code extension with filter preview sidebar
- **Workspace profiles**: Different filter configurations for different parts of a monorepo
- **Organization-level config**: Share filter profiles across a team
- **Telemetry dashboard**: Anonymized aggregate data on which commands produce the most tokens, which filters are most effective industry-wide

---

## Deliverable 19: PRD (Product Requirements Document)

### Product Name
Distill

### Problem Statement

AI coding assistants operate in token-constrained context windows. Every shell command output is injected verbatim into that context. For the 60+ most common development commands (build tools, VCS, test runners, container tools), the raw output is 50–90% noise: ANSI color codes, verbose status lines, unchanged resource states, passing test output, "entering directory" messages. This noise:
- Consumes tokens that should hold meaningful context (code, errors, intent)
- Inflates cost for paid AI APIs
- May cause the AI to miss important signals as earlier context is evicted

Developers cannot solve this themselves without manually adding `| head`, `| grep`, or `| tail` pipes to every command — which breaks automation, requires expertise, and adds cognitive overhead.

### Target User

Intermediate to senior developers using AI coding assistants in terminal workflows (Claude Code, Cursor, Gemini, Copilot). They run builds, tests, and cloud CLI tools regularly — generating significant command output that the AI processes.

Secondary: Engineering managers who pay for AI API costs and want measurable ROI on AI tooling.

### Goals

1. **G1**: Reduce tokens consumed by AI processing of shell command output by ≥40% with no developer-visible change to workflow
2. **G2**: Zero additional steps for the developer — compression is completely transparent
3. **G3**: Installation completes in under 2 minutes
4. **G4**: Support the 3 most common AI coding assistants at launch
5. **G5**: Enable community filter contribution via pip package without requiring Python code

### Non-Goals

1. Not a general-purpose output formatter for human consumption
2. Not a command wrapper developers interact with directly
3. Not an AI agent itself — Distill is middleware, not intelligence
4. Not a security tool (not designed to sanitize credentials from AI context, though we should not actively expose them)
5. Not a replacement for `less`, `grep`, or other human-facing tools

### Functional Requirements

| ID | Requirement |
|---|---|
| FR1 | Hook intercepts AI commands at PreToolUse level without developer interaction |
| FR2 | Rewrite rules support compound commands (`&&`, `\|\|`, `;`) and env prefixes |
| FR3 | Commands containing heredocs are NOT rewritten |
| FR4 | Filter pipeline has all 8 stages matching Zap's specification |
| FR5 | All filters have inline tests executable with `distill verify` |
| FR6 | Token tracking with 90-day automatic retention cleanup |
| FR7 | `distill gain` report shows cumulative savings with project scoping |
| FR8 | Project-local filters require explicit trust with SHA-256 verification |
| FR9 | `distill init` sets up the hook in one command |
| FR10 | `distill preview <command>` shows raw vs. filtered output side by side |
| FR11 | Plugin system allows `pip install distill-helm` to add new filters |

### Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR1 | Hook response latency < 50ms (parsing + rewrite, excluding subprocess) |
| NFR2 | Filter processing overhead < 200ms for outputs up to 10,000 lines |
| NFR3 | Zero network calls during normal operation (all filtering is local) |
| NFR4 | Works on Windows 10/11 with PowerShell (user's corporate environment) |
| NFR5 | Pure Python with no compiled C extensions |
| NFR6 | Plugin API is stable across minor versions (semver) |
| NFR7 | ≥80% test coverage on classifier and filter engine |
| NFR8 | No credentials (API keys, tokens) stored in SQLite tracking database |

### Developer Experience Requirements

| ID | Requirement |
|---|---|
| DX1 | Adding a new filter requires only a TOML file, no Python code |
| DX2 | `distill preview <command>` shows before/after without committing to any change |
| DX3 | Documentation explains the filter pipeline with concrete input/output examples |
| DX4 | `distill verify` catches filter regressions before deployment |

### Success Metrics

- **Primary**: Tokens consumed per AI coding session (measured via `distill gain`, compared to first week)
- **Secondary**: AI task completion rate — qualitative, self-reported (survey-based initially)
- **Adoption**: 100 GitHub stars within 3 months of open source launch
- **Distribution**: Average time-to-install < 2 minutes (measured in onboarding analytics)
- **Community**: 5 community-contributed filter plugins within 6 months

---

## Deliverable 20: Technical Design Document

### System Context

Distill is middleware between an AI coding assistant and the shell. It intercepts commands at the PreToolUse hook level, rewrites them to pass through Distill's filter, and the filter returns compressed output to the AI. The developer sees nothing different.

```
              ┌──────────────────────────────────┐
              │       AI Coding Assistant        │
              │  (Claude Code / Cursor / Gemini) │
              └──────────┬───────────────┬───────┘
           stdin JSON    │               │ reads stdout
           (command)     │               │ (compressed output)
              ┌──────────▼───────────┐   │
              │  Distill Hook Adapter│   │
              │  (parse + rewrite)   │   │
              └──────────┬───────────┘   │
           rewritten     │               │
           command       │               │
              ┌──────────▼───────────┐   │
              │   Shell Subprocess   │   │
              │  (real command runs) │   │
              └──────────┬───────────┘   │
           raw stdout    │               │
           (may be MB)   │               │
              ┌──────────▼───────────┐   │
              │   Filter Engine      │───┘
              │  (8-stage pipeline)  │
              └──────────┬───────────┘
                         │
              ┌──────────▼───────────┐
              │   SQLite Tracking    │
              │  (tokens before/after│
              │   per project)       │
              └──────────────────────┘
```

### Component Specifications

#### Component 1: HookAdapter (distill/adapters/)

**Interface:**
```python
from dataclasses import dataclass
from typing import Protocol, Optional

@dataclass
class HookInput:
    command: str       # The raw command string
    raw_json: dict     # Original parsed JSON (preserved for passthrough)
    agent: str         # "claude" | "cursor" | "gemini" | "copilot"

@dataclass
class HookOutput:
    rewritten_command: str
    original_input: HookInput
    allow: bool = True

class HookAdapter(Protocol):
    agent_name: str
    def parse_input(self, raw: bytes) -> HookInput: ...
    def format_output(self, output: HookOutput) -> bytes: ...
    def detect(self, raw: bytes) -> bool: ...
```

**Error contract**: `parse_input` must never raise. If parsing fails, it should return a `HookInput` with the original raw bytes as the command, effectively passing through unchanged. The AI session must never break due to a Distill parse error.

**Claude Code Adapter:**
- `parse_input`: Reads `tool_input.command` from JSON
- `format_output`: Writes `hookSpecificOutput.updatedInput.command` + `permissionDecision: "allow"`
- `detect`: Checks for `"hookEventName"` key in JSON

**Cursor Adapter:**
- `parse_input`: Strips doubled UTF-8 BOM (`\xEF\xBB\xBF\xEF\xBB\xBF`) before JSON parsing
- `format_output`: `{"continue": true, "permission": "allow", "updated_input": {"command": ...}}`
- `detect`: Checks for BOM prefix OR `"continue"` key in JSON

#### Component 2: Command Classifier (distill/rewrite/)

**Interface:**
```python
import re
from dataclasses import dataclass, field
from typing import Optional
from functools import lru_cache

@dataclass(frozen=True)
class Rule:
    pattern: str
    target_command: str           # "distill git"
    rewrite_prefixes: tuple[str, ...]
    category: str
    savings_pct: float
    subcmd_savings: tuple[tuple[str, float], ...] = ()

@dataclass
class ClassificationResult:
    matched: bool
    target_command: Optional[str]
    category: Optional[str]
    estimated_savings_pct: Optional[float]

# Module-level, compiled once at import
_COMPILED_RULES: list[tuple[re.Pattern, Rule]] = [
    (re.compile(rule.pattern), rule) for rule in RULES
]

@lru_cache(maxsize=2048)
def classify_command(cmd: str) -> ClassificationResult: ...

def rewrite_command(
    cmd: str,
    excluded: tuple[str, ...] = (),
    transparent_prefixes: tuple[str, ...] = (),
) -> Optional[str]: ...
```

**Implementation decisions:**

- `@lru_cache`: The same command is classified many times per session. Caching avoids repeated regex work. `maxsize=2048` is generous enough for any realistic command set.
- Patterns compiled at module import time (equivalent to Rust's `lazy_static`). Using `tuple` for frozen hashable parameters required by `lru_cache`.
- **"Last match wins"**: Iterate rules in order from general to specific; keep the last match. Specific rules appear later in `RULES`.
- **Compound splitting**: Recursive descent over lexer tokens. `MAX_DEPTH = 10` prevents infinite recursion.
- **Transparent prefix stripping**: Recursive, longest-prefix-first (sort by length descending before matching).

**Shell Lexer (`distill/rewrite/lexer.py`):**

The lexer must be quote-aware to correctly handle:
- `git commit -m "it's fixed && done"` — `&&` inside double quotes is not a compound operator
- `cat <<'EOF'\nhello && world\nEOF` — heredoc content is not parsed
- `git status 2>&1` — `&` in a redirect is not a background operator
- `cargo test & git status` — single `&` IS a background operator

Implement as a token stream: scan character by character, tracking quote state and redirect context. Token kinds: `Word`, `Operator` (`&&`, `||`), `Semicolon`, `Pipe`, `Background` (`&`), `Redirect` (`>`, `<`, `2>`, `>>`, `2>&1`, etc.).

#### Component 3: Filter Engine (distill/filters/engine.py)

**Interface:**
```python
@dataclass
class FilterDef:
    name: str
    description: str
    match_command: re.Pattern
    strip_ansi: bool = False
    replace: list[dict] = field(default_factory=list)      # [{pattern, replacement}]
    match_output: list[dict] = field(default_factory=list)  # [{pattern, message, unless?}]
    strip_lines_matching: list[re.Pattern] = field(default_factory=list)
    keep_lines_matching: list[re.Pattern] = field(default_factory=list)
    truncate_lines_at: Optional[int] = None
    head_lines: Optional[int] = None
    tail_lines: Optional[int] = None
    max_lines: Optional[int] = None
    on_empty: Optional[str] = None
    filter_stderr: bool = False

class FilterEngine:
    def apply(self, filter_def: FilterDef, output: str, command: str) -> FilterResult:
        stages_applied = []
        lines = output.splitlines()
        original_lines = len(lines)
        
        # Stage 1: strip_ansi
        if filter_def.strip_ansi:
            lines = [ANSI_RE.sub("", line) for line in lines]
            stages_applied.append("strip_ansi")
        
        # Stage 2: replace
        for rule in filter_def.replace:
            lines = [re.sub(rule["pattern"], rule["replacement"], line) for line in lines]
        if filter_def.replace:
            stages_applied.append("replace")
        
        # Stage 3: match_output (short-circuit)
        text = "\n".join(lines)
        for rule in filter_def.match_output:
            if re.search(rule["pattern"], text):
                if "unless" not in rule or not re.search(rule["unless"], text):
                    result_text = rule["message"]
                    # on_empty check
                    if not result_text.strip() and filter_def.on_empty:
                        result_text = filter_def.on_empty
                    return FilterResult(output=result_text, ...)
        
        # Stage 4: strip_lines_matching / keep_lines_matching (mutually exclusive)
        if filter_def.strip_lines_matching:
            lines = [l for l in lines if not any(p.search(l) for p in filter_def.strip_lines_matching)]
            stages_applied.append("strip_lines")
        elif filter_def.keep_lines_matching:
            lines = [l for l in lines if any(p.search(l) for p in filter_def.keep_lines_matching)]
            stages_applied.append("keep_lines")
        
        # Stage 5: truncate_lines_at
        if filter_def.truncate_lines_at:
            lines = [l[:filter_def.truncate_lines_at] for l in lines]
            stages_applied.append("truncate_lines")
        
        # Stage 6: head_lines / tail_lines
        if filter_def.head_lines and filter_def.tail_lines:
            head = lines[:filter_def.head_lines]
            tail = lines[-filter_def.tail_lines:]
            lines = head + tail
            stages_applied.append("head_tail")
        elif filter_def.head_lines:
            lines = lines[:filter_def.head_lines]
            stages_applied.append("head")
        elif filter_def.tail_lines:
            lines = lines[-filter_def.tail_lines:]
            stages_applied.append("tail")
        
        # Stage 7: max_lines
        if filter_def.max_lines and len(lines) > filter_def.max_lines:
            lines = lines[:filter_def.max_lines]
            stages_applied.append("max_lines")
        
        output_text = "\n".join(lines)
        
        # Stage 8: on_empty
        if not output_text.strip() and filter_def.on_empty:
            output_text = filter_def.on_empty
            stages_applied.append("on_empty")
        
        return FilterResult(
            output=output_text,
            original_lines=original_lines,
            filtered_lines=len(lines),
            tokens_before=estimate_tokens(output),
            tokens_after=estimate_tokens(output_text),
            stages_applied=stages_applied,
        )
```

**ANSI stripping pattern**: `re.compile(r'\x1b\[[0-9;]*[mGKHJABCDFE]')` — covers cursor movement, color, and erase sequences.

**Error contract**: `apply()` must never raise. Any regex error is logged to stderr and the stage is skipped. Returning unfiltered output is always better than crashing.

#### Component 4: TOML Filter Registry (distill/filters/toml_loader.py)

```python
import importlib.resources
import tomllib    # Python 3.11+ stdlib; use tomli for 3.9/3.10
import re
from pathlib import Path
from typing import Optional

class FilterRegistry:
    """Three-tier lookup: project-local > user-global > built-in"""
    
    def __init__(self):
        self._filters: list[FilterDef] = []
        self._load_builtin()
        self._load_user_global()
        self._load_project_local()
    
    def lookup(self, command: str) -> Optional[FilterDef]:
        """Return first filter whose match_command regex matches `command`."""
        for f in self._filters:
            if f.match_command.search(command):
                return f
        return None
    
    def _load_builtin(self):
        pkg = importlib.resources.files("distill.filters.builtin")
        for resource in sorted(pkg.iterdir()):
            if resource.name.endswith(".toml"):
                self._parse_and_add(resource.read_text("utf-8"), source="builtin")
    
    def _load_user_global(self):
        path = user_config_dir() / "filters.toml"
        if path.exists():
            self._parse_and_add(path.read_text("utf-8"), source="user-global")
    
    def _load_project_local(self):
        path = find_project_root() / ".distill" / "filters.toml"
        if path.exists() and trust_manager.is_trusted(path):
            self._parse_and_add(path.read_text("utf-8"), source="project-local")
        elif path.exists():
            print(f"[distill] Project filter found but not trusted. Run: distill trust {path}", file=sys.stderr)
```

**Built-in filter distribution**: TOML files included in the Python package via `importlib.resources`. List them in `pyproject.toml`:
```toml
[tool.setuptools.package-data]
"distill.filters.builtin" = ["*.toml"]
```
No build step required — Python's package data mechanism handles this.

#### Component 5: SQLite Tracking (distill/tracking/db.py)

```python
import sqlite3
import math
from pathlib import Path
from platformdirs import user_data_dir
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    project_path TEXT NOT NULL,
    tokens_before INTEGER NOT NULL DEFAULT 0,
    tokens_after INTEGER NOT NULL DEFAULT 0,
    exec_time_ms INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_project ON commands(project_path);
CREATE INDEX IF NOT EXISTS idx_recorded_at ON commands(recorded_at);

CREATE TABLE IF NOT EXISTS parse_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    raw_input TEXT NOT NULL,
    error TEXT NOT NULL,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)

class TrackingDb:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or (Path(user_data_dir("distill")) / "tracking.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn
    
    def record(self, command: str, project_path: str,
               tokens_before: int, tokens_after: int, exec_time_ms: int):
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO commands (command, project_path, tokens_before, tokens_after, exec_time_ms) VALUES (?,?,?,?,?)",
                    (command, project_path, tokens_before, tokens_after, exec_time_ms)
                )
                # 90-day auto-cleanup
                conn.execute("DELETE FROM commands WHERE recorded_at < datetime('now', '-90 days')")
        except Exception as e:
            print(f"[distill] Tracking write failed (continuing): {e}", file=sys.stderr)
    
    def project_gain(self, project_path: str) -> dict:
        with self._get_connection() as conn:
            # GLOB not LIKE — avoids _ wildcard matching unintended paths
            rows = conn.execute(
                "SELECT SUM(tokens_before), SUM(tokens_after), COUNT(*) FROM commands "
                "WHERE project_path GLOB ? AND tokens_before > 0",
                (f"{project_path}*",)
            ).fetchone()
            return {"tokens_before": rows[0] or 0, "tokens_after": rows[1] or 0, "commands": rows[2] or 0}
```

**Windows path handling**: On Windows, `project_path` will contain backslashes. The GLOB pattern must use `\\*` instead of `/*`. Use `Path(project_path).as_posix()` for consistent GLOB matching, or normalize to forward slashes on storage.

#### Component 6: Configuration (distill/config/)

```python
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
from platformdirs import user_config_dir
import tomllib
import os

@dataclass
class HooksConfig:
    exclude_commands: list[str] = field(default_factory=list)
    transparent_prefixes: list[str] = field(default_factory=list)

@dataclass
class TrackingConfig:
    enabled: bool = True
    history_days: int = 90
    database_path: Optional[str] = None

@dataclass
class LimitsConfig:
    grep_max_results: int = 200
    grep_max_per_file: int = 25
    status_max_files: int = 15
    status_max_untracked: int = 10
    passthrough_max_chars: int = 2000

@dataclass
class Config:
    hooks: HooksConfig = field(default_factory=HooksConfig)
    tracking: TrackingConfig = field(default_factory=TrackingConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    
    @classmethod
    def load(cls) -> "Config":
        path = Path(user_config_dir("distill")) / "config.toml"
        if not path.exists():
            return cls()
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            return cls._from_dict(data)
        except Exception:
            return cls()  # Never fail on config load
    
    @classmethod
    def _from_dict(cls, data: dict) -> "Config":
        # Use dataclasses.replace or manual parsing
        ...
```

**Config location**: `platformdirs.user_config_dir("distill")` returns:
- Linux: `~/.config/distill`
- macOS: `~/Library/Application Support/distill`
- Windows: `%APPDATA%\distill` (e.g., `C:\Users\username\AppData\Roaming\distill`)

**Environment variable overrides** (checked before config file):
- `DISTILL_NO_FILTER=1` — bypass filter engine
- `DISTILL_DEBUG=1` — enable debug output
- `DISTILL_DB_PATH` — override SQLite path
- `DISTILL_DISABLED=1` (command prefix) — skip rewrite for that command

#### Component 7: Dependencies

**Core (no optional dependencies for basic operation):**
```toml
[project.dependencies]
typer = ">=0.9"           # CLI framework (wraps Click)
tomli = ">=2.0; python_version < '3.11'"  # TOML parsing (stdlib in 3.11+)
platformdirs = ">=3.0"   # Cross-platform config/data directories
```

**Standard library only for core logic:**
- `sqlite3` — tracking database
- `re` — regex (pattern compilation, matching)
- `importlib.resources` — built-in filter files
- `subprocess` — command execution
- `hashlib` — SHA-256 for trust system
- `functools` — `lru_cache` for classifier
- `dataclasses` — all data structures
- `pathlib` — path handling

**Optional (for enhanced features):**
```toml
[project.optional-dependencies]
display = ["rich>=13.0"]  # Colored output, tables, diff view
```

**Deliberately excluded:**
- No AI SDK in core (Distill is infrastructure)
- No async framework (synchronous is sufficient)
- No ORM (raw `sqlite3` is appropriate)
- No compiled C extensions (pure Python is a hard requirement)

#### Component 8: Error Handling Philosophy

**In the hook adapter**: Never raise. If parsing fails, log to stderr and return the original command unchanged. The AI session must never receive an error from the hook — it would break the entire session.

```python
def handle_hook(raw: bytes, adapter: HookAdapter) -> bytes:
    try:
        hook_input = adapter.parse_input(raw)
        rewritten = rewrite_command(hook_input.command)
        if rewritten is None:
            return raw  # No rewrite — passthrough
        output = HookOutput(rewritten_command=rewritten, original_input=hook_input)
        return adapter.format_output(output)
    except Exception as e:
        print(f"[distill] hook error (passing through): {e}", file=sys.stderr)
        return raw  # Always passthrough on error
```

**In the filter engine**: Never raise. On any error, return the unfiltered output. Partial filtering is acceptable; crashing is not.

**In tracking**: Never raise. If SQLite write fails, log and continue. Tracking failure never blocks command execution.

**In config loading**: Never raise. Any config parse error returns defaults. Missing config file returns defaults.

**In trust verification**: Fail safe. If the trust database is unreadable, treat all project-local filters as untrusted. Log the error.

#### Component 9: Testing Strategy

**Unit tests (fast, no subprocess, no filesystem):**
- `test_classifier.py`: 100+ cases — one per rule, edge cases (heredocs, compound commands, env prefixes, path normalization)
- `test_engine.py`: Each pipeline stage in isolation; combined stage tests; `on_empty` behavior
- `test_adapters.py`: JSON round-trip for each agent (Claude, Cursor, Gemini, Copilot)
- `test_trust.py`: Trust grant, trust verify, content-changed detection

**TOML inline tests (run via `distill verify`):**
```toml
[[tests.make]]
name = "strips entering/leaving lines"
input = "make[1]: Entering directory '/home'\ngcc -O2 foo.c\nmake[1]: Leaving directory '/home'\n"
expected = "gcc -O2 foo.c"
```
Every filter ships with inline tests. `distill verify` runs all of them. CI runs `distill verify` to catch filter regressions.

**Integration tests (subprocess, requires tools installed):**
- `git status` → verify output structure is preserved; token count reduced
- `pytest` → verify failures are shown; passing tests stripped
- Mark with `@pytest.mark.integration` — skipped in CI if tools not available

**Property tests (hypothesis, optional):**
- Classifier: Any string input → either `ClassificationResult.matched=True` or `matched=False`, never raises
- Filter engine: `tokens_after <= tokens_before` always (filter never inflates output)
- Lexer: Round-trip property — splitting and rejoining compound commands preserves semantics for simple cases

#### Component 10: Windows/Corporate Environment Compatibility

The user's primary environment is Windows with restrictions on installing compiled software. All design decisions account for this:

| Constraint | Design Response |
|---|---|
| No Rust/C binaries | Pure Python, no compiled extensions |
| Corporate app restrictions | `pip install --user` works without admin rights |
| PowerShell primary shell | Hook script is PowerShell-compatible (`.ps1`) |
| Windows paths with `\` | `platformdirs` for correct paths; normalize to `/` for SQLite GLOB |
| `%APPDATA%` for config | `platformdirs.user_config_dir()` returns correct Windows path |
| No system SQLite | `sqlite3` is bundled in Python standard library |
| Restricted filesystem | Config dir creation uses `mkdir(parents=True, exist_ok=True)` |
| BOM in files | UTF-8-sig encoding handling where Windows tools produce BOM |

**PowerShell hook script:**
```powershell
# distill-hook.ps1 (installed by distill init --claude)
$input_json = $input | Out-String
python -m distill hook claude $input_json
```
The hook is a PowerShell script that pipes stdin to the Python module. No binary required.

---

## Appendix: Key Source Locations (for Reference)

| Concept | File | Lines |
|---|---|---|
| Command classification | `src/discover/registry.rs` | 94–198 |
| Command rewriting | `src/discover/registry.rs` | 462–828 |
| Heredoc detection | `src/discover/registry.rs` | 230–234 |
| Compound command splitting | `src/discover/registry.rs` | 494–601 |
| TOML filter pipeline | `src/core/toml_filter.rs` | entire file |
| Code filter (file reads) | `src/core/filter.rs` | entire file |
| SQLite tracking | `src/core/tracking.rs` | entire file |
| Claude Code hook | `src/hooks/hook_cmd.rs` | ~1–200 |
| Cursor BOM stripping | `src/hooks/hook_cmd.rs` | ~200–350 |
| Hook installation | `src/hooks/init.rs` | entire file |
| Discovery rules | `src/discover/rules.rs` | entire file |
| Configuration | `src/core/config.rs` | entire file |
| Main router | `src/main.rs` | entire file (3,221 lines) |
