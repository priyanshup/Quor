# Distill Design Review — Pre-Implementation Panel

> Five-role critical review: Principal Software Architect · Open Source Maintainer · Performance Engineer · Product Manager · Developer Advocate
> Produced before any code is written. Every claim is grounded in the Zap analysis already completed.

---

## 1. Compression Algorithm Analysis — Prioritization Matrix

Scoring: 1 (low) → 5 (high/severe) per dimension.
Risk = risk of changing meaning that matters to the AI.
Cost = computational cost to apply per invocation.

### Pipeline Stage Ranking

| Stage | Token Impact | Meaning Risk | Comp. Cost | Impl. Complexity | MVP Priority | Notes |
|---|---|---|---|---|---|---|
| `strip_ansi` | 3 | 1 | 1 | 1 | **Critical** | Zero risk, applies universally, trivial to implement. Include in every filter by default. |
| `strip_lines_matching` | 5 | 3 | 2 | 2 | **Critical** | Highest impact when patterns are tight (e.g., pytest PASSED lines). Risk depends entirely on pattern quality. |
| `max_lines` | 4 | 2 | 1 | 1 | **Critical** | Safety net for every filter. Without it, pathological outputs break everything. Must exist in MVP. |
| `on_empty` | 1 | 1 | 1 | 1 | **Critical** | Zero token impact but prevents AI misinterpreting empty output as failure. UX correctness, not compression. |
| `match_output` (short-circuit) | 5 | 4 | 2 | 3 | **High** | Collapses entire output to one line. Highest risk stage — if pattern is wrong, AI gets false summary. The `unless` guard mitigates this but adds complexity. |
| `head_lines` | 4 | 3 | 1 | 1 | **High** | Predictable truncation. Risk: middle content is always lost. Acceptable for test runners (errors at top) but wrong for tools that put summary at end. |
| `tail_lines` | 3 | 3 | 1 | 1 | **High** | Opposite of head. Good for tools where summary is last (terraform plan). Less useful for test output. |
| `keep_lines_matching` | 5 | 5 | 2 | 2 | **Medium** | Highest risk stage in the pipeline. Inverted filter — everything NOT matching is destroyed. One incorrect pattern silently removes critical output. Should carry explicit warnings in documentation. |
| `replace` | 2 | 2 | 3 | 3 | **Medium** | Normalization, not elimination. Low impact but enables subsequent stages (e.g., replace abs paths → relative before stripping). Regex with backreferences is complex to get right. |
| `truncate_lines_at` | 2 | 2 | 1 | 1 | **Low** | Addresses a narrow problem (very long individual lines: SQL dumps, base64 blobs). Rarely needed. |
| `filter_stderr` | 3 | 3 | 1 | 2 | **Low** | Useful for commands that emit noise to stderr (cargo warnings). Risk: stderr may contain the error the AI needs. Off by default is correct. |

### Discovery Rule Ranking (Which Commands to Filter First)

| Command | Session Frequency | Raw Token Volume | Filter Difficulty | Risk | MVP Priority |
|---|---|---|---|---|---|
| `git diff` | Very High | Very High | Low | Low | **1 — Must have** |
| `git log` | Very High | High | Low | Low | **2 — Must have** |
| `pytest` / `python -m pytest` | High | Very High | Medium | Medium | **3 — Must have** |
| `git status` | Very High | Medium | Low | Low | **4 — Must have** |
| `cat` / file read (code) | Very High | High | High | Medium | **5 — Must have** |
| `mypy` | Medium | High | Low | Low | **6 — High** |
| `tsc --noEmit` | Medium | High | Low | Low | **7 — High** |
| `cargo test` | Medium | Very High | Medium | Medium | **8 — High** |
| `make` | Medium | Medium | Low | Low | **9 — High** |
| `terraform plan` | Low-Medium | High | Low | Low | **10 — Medium** |
| `docker ps` / `docker logs` | Medium | Medium | Low | Low | **11 — Medium** |
| `npm/pnpm install` | Medium | High | Low | Low | **12 — Medium** |
| `kubectl get` / `kubectl describe` | Low | Medium | Medium | Medium | **13 — Later** |
| `gh pr list` / `gh issue list` | Low | Medium | Low | Low | **14 — Later** |
| `ruff check` / `eslint` | Medium | High | Low | Low | **15 — High** |

### Critical Insight From This Ranking

`keep_lines_matching` and `match_output` are the highest-risk stages. Both can silently destroy information. They should be:
1. Prominently documented as "advanced, use with caution"
2. Tested with exhaustive inline test cases
3. Potentially preceded by a `distill preview` sanity check when authoring

`strip_lines_matching` is the workhorse — high impact, manageable risk, low cost. It should be the default recommendation for new filter authors.

The most important discovery rule is `git diff`. It has the highest combination of session frequency × token volume × filter safety. If we get nothing else right, getting git diff right is the win.

---

## 2. Pipeline Execution Dependencies

### Complete Pipeline with Dependencies

```
INPUT: raw command output (string)
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 1: strip_ansi                                         │
│                                                             │
│ Removes: \x1b[31m, \x1b[0m, \x1b[2K, cursor codes         │
│ Input: raw string with embedded escape codes                │
│ Output: clean string                                        │
│                                                             │
│ Position: MUST BE FIRST                                     │
│ Why: All subsequent regex stages operate on text. ANSI      │
│ codes corrupt regex matching. "\x1b[31mError:" does NOT     │
│ match "^Error:" without stripping first.                    │
│ Dependencies: None.                                         │
│ Dependents: ALL subsequent stages.                          │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 2: replace                                            │
│                                                             │
│ Applies: regex substitutions, line by line                  │
│ Example: "/home/user/project/src" → "src/"                  │
│                                                             │
│ Position: MUST BE SECOND (after strip_ansi)                 │
│ Why: Normalizes content so subsequent filters see           │
│ canonical form. A path normalization here ensures           │
│ strip_lines_matching works correctly on the normalized      │
│ form. If replace runs after strip, lines that should be     │
│ normalized and then stripped survive.                       │
│ Dependencies: Stage 1 (ANSI must be clean for regex)        │
│ Dependents: Stages 3, 4 (operate on normalized content)     │
│                                                             │
│ RISK: Multiple replace rules are applied in sequence.       │
│ Rule N output is input to Rule N+1. Order within replace    │
│ matters and is user-controlled.                             │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 3: match_output (SHORT-CIRCUIT)                       │
│                                                             │
│ Checks: does the full text blob match pattern P?            │
│ If yes (and unless-guard is absent): replace ALL output     │
│ with a brief message and SKIP remaining stages.             │
│                                                             │
│ Position: MUST BE THIRD (after normalize, before line ops)  │
│ Why: Operates on the FULL normalized text. It needs to see  │
│ the complete output to make a short-circuit decision. If    │
│ it ran after strip_lines_matching, the text it checks would │
│ already be partially filtered — the short-circuit pattern   │
│ might fail to match content that was stripped.              │
│                                                             │
│ Example: "cargo check" with only "Finished" in output.      │
│ match_output collapses to "cargo check: ok".                │
│ If run after head_lines, "Finished" might not appear in     │
│ the truncated head.                                         │
│                                                             │
│ Dependencies: Stages 1, 2 (needs clean, normalized text)    │
│ Dependents: Stages 4–8 are skipped if this fires            │
│                                                             │
│ EXIT PATH → on_empty → OUTPUT                               │
└─────────────────┬───────────────────────────────────────────┘
                  │ (only if match_output did NOT short-circuit)
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 4: strip_lines_matching  OR  keep_lines_matching      │
│          (MUTUALLY EXCLUSIVE)                               │
│                                                             │
│ strip: discard lines matching any of the patterns           │
│ keep:  discard lines NOT matching any of the patterns       │
│                                                             │
│ Position: MUST BE FOURTH                                    │
│ Why: Must run on the normalized, full text (after 1+2).     │
│ Must run BEFORE head/tail (stages 6) because head/tail      │
│ should select from the MEANINGFUL subset, not from the raw  │
│ output. If we head-first-50-lines and then strip, we might  │
│ strip those 50 lines entirely and be left with nothing.     │
│                                                             │
│ What breaks if swapped with Stage 6 (head/tail):           │
│ Test runner with 500 failing tests and 1000 passing tests.  │
│ If we head(100) first, we get the first 100 lines, most     │
│ passing. Then strip(PASSED) removes them, leaving 10 lines. │
│ Correct order: strip(PASSED) first → 500 lines remaining → │
│ head(100) → first 100 failures. Completely different result.│
│                                                             │
│ Dependencies: Stages 1, 2, 3                                │
│ Dependents: Stages 6, 7 (operate on reduced line set)       │
│                                                             │
│ ARCHITECTURAL NOTE: these are mutually exclusive but there  │
│ is no parse-time validation that both are not set. Distill  │
│ MUST validate this at filter load time and return an error. │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 5: truncate_lines_at                                  │
│                                                             │
│ Truncates individual lines longer than N characters.        │
│                                                             │
│ Position: FIFTH (after line filtering, before selection)    │
│ Why: Truncation affects line content, not line count. It    │
│ should run after we've decided WHICH lines to keep (stage 4)│
│ and before we select HOW MANY (stage 6). A line that's      │
│ 10,000 chars should be truncated before head/tail counts it │
│ in the line budget.                                         │
│                                                             │
│ What breaks if before Stage 4:                              │
│ A strip pattern might match a line that gets truncated AFTER│
│ the match check. The match works on the full line; the      │
│ truncated version is what the AI sees. Ordering issue is    │
│ semantic, not catastrophic, but is confusing.               │
│                                                             │
│ INDEPENDENT OF: head_lines, tail_lines (commutative)        │
│ Could run before or after Stage 6 with equivalent result.   │
│ Zap chose this ordering; Distill should match for compat.   │
│                                                             │
│ Dependencies: Stage 4                                       │
│ Dependents: Stage 6 (head/tail sees truncated lines)        │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 6: head_lines  AND/OR  tail_lines                     │
│          (CAN COMBINE BOTH)                                 │
│                                                             │
│ head: keep first N lines                                    │
│ tail: keep last M lines                                     │
│ both: keep first N + last M (may overlap)                   │
│                                                             │
│ Position: SIXTH (after content filtering)                   │
│ Why: Selects from the MEANINGFUL subset produced by         │
│ stages 4 and 5. Selection before filtering is the order     │
│ error described in Stage 4.                                 │
│                                                             │
│ OVERLAP BEHAVIOR: If head(30) + tail(30) and there are only │
│ 40 lines, the result may be 40 lines (union of first 30 +   │
│ last 30 = all 40). Define and document this behavior.       │
│ Zap's Rust: concat head and tail slices; deduplication is   │
│ not performed. Distill must match this or document           │
│ explicitly.                                                 │
│                                                             │
│ Dependencies: Stages 1–5                                    │
│ Dependents: Stage 7 (max_lines applies after selection)     │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 7: max_lines                                          │
│                                                             │
│ Absolute cap: if output > N lines, truncate to N.           │
│                                                             │
│ Position: SEVENTH (last line-count operation)               │
│ Why: Safety net. After head+tail selection, the combined    │
│ result might exceed max_lines (if head+tail both specified  │
│ and sum > max_lines). max_lines is the final guarantee.     │
│                                                             │
│ What if before Stage 6:                                     │
│ max_lines(50) then tail(10) → tail takes from the 50-line   │
│ result, which is correct. But head(30)+tail(30) on a        │
│ max_lines(50) output gives 50 lines, then head+tail takes   │
│ first 30 + last 30 from the 50 = 50 (correct but           │
│ semantically confusing). Order matters for combined cases.  │
│                                                             │
│ Dependencies: Stage 6                                       │
│ Dependents: Stage 8                                         │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 8: on_empty                                           │
│                                                             │
│ If output is empty (or whitespace-only): emit fallback msg  │
│ Example: "make: ok", "terraform plan: no changes detected"  │
│                                                             │
│ Position: LAST — always                                     │
│ Why: By definition, checks whether all previous stages      │
│ produced empty output. Cannot run before anything.          │
│                                                             │
│ BEHAVIORAL NOTE: Zap also applies on_empty after the        │
│ match_output EXIT PATH. Distill must handle both paths:     │
│ (a) match_output fires → message is the fallback check      │
│ (b) pipeline completes → check on_empty                     │
│                                                             │
│ Dependencies: ALL previous stages                           │
│ Dependents: None                                            │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
              OUTPUT: compressed string → AI context window
```

### Stages That Are Completely Independent

| Pair | Independent? | Notes |
|---|---|---|
| `strip_ansi` ↔ anything | NOT independent | Must always be first |
| `replace` ↔ `strip_lines_matching` | NOT independent | Replace normalizes content that strip uses |
| `strip_lines_matching` ↔ `keep_lines_matching` | Independent of each other | Mutually exclusive; either can run |
| `head_lines` ↔ `tail_lines` | Independent of each other | Can both run; combined order is commutative |
| `truncate_lines_at` ↔ `head_lines`/`tail_lines` | Independent | Can swap; Zap chose truncate-before-select |
| `max_lines` ↔ `on_empty` | NOT independent | max_lines must precede on_empty |

### The One Ordering Bug to Avoid

The most dangerous incorrect ordering is `head_lines` BEFORE `strip_lines_matching`. It looks intuitive ("grab the first 50 lines, then clean them") but produces catastrophically wrong results for test runners and build tools where meaningful content (failures) is interspersed with noise (passing tests). Always: filter content first, then select range.

---

## 3. Distinguishing Algorithms in Zap

Separating genuine insight from routine implementation.

### Genuinely Innovative

**1. RegexSet for O(1) rule dispatch**

The `RegexSet` compiles all 30+ rule patterns into a single automaton and checks them all in one pass over the command string. This is the Aho-Corasick insight applied to multiple-regex matching — the cost is proportional to the input length, not to the number of rules.

Why it matters: The hook runs synchronously in the AI's request path. An O(n×m) sequential match (n rules × m input) would add measurable latency as rules accumulate. The RegexSet grows the rule set for free.

Python equivalent: Python's `re` module doesn't have `RegexSet`. Use `re.compile("|".join(f"(?P<r{i}>{r.pattern})" for i, r in enumerate(RULES)))` for a similar effect, or use `regex` package which has `regex.Pattern.fullmatch` alternatives. This is a design decision we must make explicitly — Python's native approach requires careful construction.

**2. Last-match wins for specificity (via rule ordering)**

Rules in the `RULES` array are ordered general-to-specific. The last match wins. `npm exec tsc` matches both the generic `npm exec|run` rule AND the specific `tsc` rule. Because the `tsc` rule appears later in the array and `matches.last()` is used, the correct specific handler wins — no priority field, no explicit conflict resolution logic.

Why it matters: It's an elegant encoding of a common pattern. The ordering IS the priority. This is zero-cost: no priority sorting at runtime, no explicit conflict detection.

Python equivalent: Preserve this in our rule table design. The `RULES` list order must be general-to-specific, and we take the last match. Document this invariant prominently.

**3. Compound command splitting with per-operator semantics**

Not all operators are treated equally:
- `&&`, `||`, `;` → split into independent segments, each classified and potentially rewritten
- `|` (pipe) → left side may be rewritten; pipe target (right side) stays raw
- `&` (background) → both sides classified independently
- `$((` → no split (arithmetic can contain operators)

The `find`/`fd` pipe exemption is a second-order insight: even when the left side of a pipe would normally be rewritten, `find | xargs` must not be touched because `zap find` produces a different format than raw find output.

Why it matters: Naive `&&` splitting gets most cases right. Getting the pipe semantics and the `find` exemption right is what separates a tool that works from a tool that works reliably.

Python equivalent: Our lexer must implement this full operator taxonomy. The `find`-in-pipe exemption must be explicitly documented as a design decision, not left as an implicit regex exclusion.

**4. Transparent prefix recursion with depth limiting**

```
docker exec mycontainer git status
→ strip "docker exec mycontainer" (user-configured prefix)
→ classify "git status" → matched
→ rewrite "git status" → "distill git status"
→ prepend prefix: "docker exec mycontainer distill git status"
```

This is recursive: a command can have multiple wrapper prefixes, each stripped in turn. The recursion depth limit (10) is not just defensive programming — it enables genuinely complex prefix stacks (`sudo env FOO=1 exec builtin noglob git status`) while preventing pathological configs.

Why it matters: Without transparent prefix support, any developer using Docker-in-docker, poetry run, direnv exec, or nix develop sees zero filtering on all their commands. This is a significant real-world use case.

Python equivalent: Implement the same recursive strip-classify-prepend pattern. The depth limit should be configurable (default 10).

**5. Passthrough with zero-token recording**

When a command has no filter, it executes normally and is recorded with `tokens_before=0, tokens_after=0`. The analytics layer excludes zero-token records from savings calculations.

Why it matters: Without this, two problems arise. (1) Passthrough commands inflate the "commands filtered" count, misleading users about coverage. (2) If passthrough commands are excluded from the database entirely, the execution log has gaps, making it harder to understand what the AI actually ran.

The zero-token trick: complete execution log + accurate savings statistics + no code complexity. Clean.

Python equivalent: Same pattern, but make `0/0` sentinel explicit in the schema with a boolean `is_passthrough` column. Cleaner than inferring from zero values.

**6. `match_output` with `unless` guard (two-level pattern matching)**

```toml
[[filters.cargo.match_output]]
pattern = "^Finished"
message = "cargo check: ok"
unless = "warning|error"
```

If the output contains "Finished" AND does NOT contain "warning" or "error": replace the entire output with "cargo check: ok". This is a two-level logical gate: affirmative condition AND negative condition.

Why it matters: Without `unless`, you'd need to enumerate every possible "warning present" case to avoid false short-circuiting. The `unless` guard makes the filter robust without requiring exhaustive enumeration.

Python equivalent: Implement exactly. Consider naming it `unless_pattern` for clarity.

**7. Build-time asset embedding**

The `build.rs` concatenates 59+ TOML files into a single blob embedded in the binary. Zero I/O, zero filesystem access, zero startup cost.

Why it matters: The hook binary starts and exits per command. Startup time matters. Reading 59 files from disk on startup would add 50-200ms of I/O. The embedded blob is instant.

Python equivalent: `importlib.resources` achieves the same result — TOML files are included in the Python package and read once at import time. The key insight to preserve: compile/validate ALL built-in filters at import time (module-level code), not on first use. One startup cost, zero per-invocation cost.

**8. Quote-aware lexer for heredoc detection (not string-matching)**

```rust
pub fn has_heredoc(cmd: &str) -> bool {
    tokenize(cmd).iter().any(|t| t.kind == TokenKind::Redirect && t.value.starts_with("<<"))
}
```

The lexer tokenizes the command string, tracking quote state. A `<<` inside `"echo 'use << here'"` is correctly identified as string content, not a heredoc marker.

Why it matters: String-matching `cmd.contains("<<")` gives false positives and could prevent rewriting valid commands like `git commit -m "use <<EOF here"`. The quote-aware lexer is the correct solution.

Python equivalent: Implement a minimal state machine lexer. It does NOT need to handle all shell constructs — only enough to correctly identify quote boundaries and redirect tokens. This is ~100 lines of correct Python; do not use a shell parsing library for this (too heavy).

### NOT Innovative (Ordinary Engineering)

- ANSI stripping: a well-known single regex
- Line truncation: a list slice
- `max_lines` cap: a list slice
- SQLite WAL mode: documented best practice
- SHA-256 for trust: standard cryptographic practice
- The 8-stage pipeline itself: a sensible sequencing of well-understood string operations

The innovation in Zap is not in any individual technique but in the combination: a complete, safe, extensible system built from individually simple parts. The architecture is the insight.

---

## 4. Distill Engineering Philosophy

These are proposed as formal principles for the contributor documentation. Each has a rationale and a test you can apply.

---

**Principle 1: Meaning preservation is non-negotiable.**

Distill is not a general-purpose compressor. It is a context optimizer for AI assistants. If the AI needs a piece of information to complete its task correctly, that information must survive filtering — regardless of how many tokens it costs.

*Test: Before shipping a new filter, ask: "Could an AI make a wrong decision because of what this filter removes?" If yes, the filter is too aggressive.*

*Corollary: When uncertain whether to strip a line, keep it.*

---

**Principle 2: Fail transparent, never silent.**

When a filter encounters an error — regex catastrophic backtracking, unexpected encoding, a parse failure — it returns the unfiltered output, logs the error to stderr, and continues. The AI session must never be broken by a Distill failure.

*Test: Any exception inside the filter pipeline must be caught at the engine boundary and result in passthrough, not crash.*

*Corollary: The hook adapter is also covered. A hook that crashes produces no output, which the AI interprets as permission denied. This is categorically unacceptable.*

---

**Principle 3: Measure before you optimize.**

Token savings are a means, not an end. The actual goal is improved AI task quality. Every filter ships with baseline measurements. We do not accept a filter that claims "80% savings" without a corresponding inline test suite demonstrating what is retained and what is removed.

*Test: Can you show a concrete before/after example for this filter? If not, the filter is not ready.*

---

**Principle 4: Conservative defaults, explicit opt-in for aggression.**

Distill ships with defaults that err on the side of including more content. `max_lines = 200` rather than `max_lines = 50`. `keep_lines_matching` is never a default. Aggressive compression — `keep_lines_matching`, `match_output`, `Aggressive` code filter — must be explicitly configured by the user.

*Test: Would a developer unfamiliar with Distill be surprised by what the default filter removes? If yes, that default is wrong.*

---

**Principle 5: One interface, many implementations.**

Filters can be implemented as TOML (declarative), Python (programmatic), or eventually as AI model calls (semantic). All three conform to the same `FilterPlugin` protocol. The filter engine doesn't know or care which implementation runs.

*Test: Can you swap a TOML filter for a Python filter for the same command without changing any caller? If not, the abstraction boundary is wrong.*

---

**Principle 6: Transparency before performance.**

A filter that cannot explain what it did is a black box. Every filter invocation records: what stage fired, how many lines were removed at each stage, what the input and output token counts were. `distill explain <command>` is a first-class feature, not a debug flag.

*Test: Can a user understand exactly what Distill did to the AI's output without reading source code? If no, we haven't done our job.*

---

**Principle 7: Platform is not an afterthought.**

Windows, PowerShell, corporate firewalls, restricted filesystems, and proxy servers are first-class environments. We test on Windows CI from the first commit. We never write `~` when we mean `user_home_dir()`. We never write `/tmp` when we mean `tempfile.mkdtemp()`.

*Test: Does this code path work on Windows with a corporate proxy and AppData redirection? If you don't know, it probably doesn't.*

---

**Principle 8: Every filter must be verifiable.**

Built-in filters ship with inline tests. User-contributed filters in pull requests require inline tests. `distill verify` runs as a CI step. A filter with no tests is a bug waiting to happen.

*Test: Can `distill verify` tell you, in CI, whether a filter change broke existing behavior? If not, the tests are insufficient.*

---

**Principle 9: The plugin interface is a contract.**

Once the plugin API is released (v1.0), we treat it as a public contract. Breaking changes require a major version bump. Community plugin authors are stakeholders.

*Test: Before any API change, ask: "Does this break existing plugins?" If yes, it requires a major version and migration guide.*

---

**Principle 10: Name things once.**

The product name is Distill. The config directory is `~/.config/distill`. Environment variables start with `DISTILL_`. Error messages say "distill:". The Python package is `distill`. There is no internal alias, no legacy naming, no "rtk" equivalent.

*Test: Grep the codebase for any alias to the product name. If any exist, remove them before release.*

---

## 5. Product Differentiation: Why Distill Instead of Zap?

*This is the answer we give a developer who just discovered both tools.*

---

**The honest answer first: if you are on macOS or Linux, already have a Rust toolchain, and just want token savings with no friction — Zap works today and works well. This is not a criticism of Zap. It is the right tool for that profile.**

Distill is built for a different set of requirements:

---

**You should install Distill if:**

**1. You are on Windows.**

Zap distributes a pre-built Linux/macOS binary. On Windows, you either compile from source (requires Rust, which requires corporate IT approval) or you find a pre-built binary if one exists for your architecture. Distill is `pip install distill` — one command, no compilation, no admin rights required. It runs wherever Python runs, which on Windows in 2026 is effectively everywhere.

**2. You want to understand what was filtered.**

`distill explain "pytest tests/"` shows you, line by line, what each pipeline stage removed and why. `distill preview "git log --oneline -20"` opens a side-by-side diff: raw output on the left, what the AI sees on the right. Zap gives you a `zap gain` number. Distill gives you a chain of evidence.

**3. You need filters Zap doesn't ship.**

`pip install distill-helm` and Helm commands are filtered. `pip install distill-pulumi` and Pulumi output is filtered. Community filters are Python packages distributed through PyPI — the same mechanism you already use for every other tool in your stack. You do not need to fork the project or compile a binary to extend Distill.

**4. You are on a Python team and you want to write your own filters.**

Distill's built-in filters are TOML. Your custom filters can also be TOML — or Python if you need logic beyond pattern matching. A Python filter for your specific internal deployment tool is twenty lines of code and a `pyproject.toml` entry point. Your team can read and review it in the same code review as everything else.

**5. You want data about whether filtering is actually helping.**

`distill gain` goes further than token counts. `distill health` tells you which filters are most used, which are saving the least, and which haven't fired in 30 days. If a filter is silently ineffective because your tool updated its output format, `distill health` surfaces that — Zap does not.

---

**What Distill does not offer (be honest):**

- Distill will not be faster than Zap's compiled binary at startup. Zap starts in <10ms. Distill starts in 50-100ms (Python interpreter startup). For most AI sessions, this difference is invisible. For extremely high-frequency command execution, it may be noticeable.
- Distill will not have binary distribution. You need Python 3.11+ installed. This is a non-issue for developers; it may matter in constrained CI environments.

---

**The positioning statement:**

*Zap is a fast, purpose-built token reducer. Distill is a transparent, extensible, cross-platform context optimizer. If you need speed and have a Unix environment: Zap. If you need transparency, Windows support, community extensibility, or Python integration: Distill.*

---

## 6. Trust Features

Trust is the hardest problem in Distill. The tool operates between the AI and the shell without the developer seeing what it does. The developer must trust that what the AI sees is accurate. We must earn that trust systematically.

Recommended features, in priority order:

---

**Tier 1: Essential for launch**

**`distill preview <command>`**
Runs the command, captures output, applies all matching filters, shows a three-pane view: raw output, filtered output, and what was removed (the diff). This is the single most important trust feature. Without it, Distill is a black box.

**`distill explain <command>` (or `--explain` flag)**
After running a filtered command, prints a trace: "Stage strip_ansi: removed 1,247 chars. Stage strip_lines_matching (pattern '^make\[\d+\]:'): removed 18 lines. Stage max_lines: capped at 50 (was 62 lines). Result: 44 lines, 312 tokens (was 1,847 tokens, 83% reduction)." Every decision explained, every number provided.

**`distill verify [filter]`**
Runs all inline tests for all filters (or a named filter). Output: pass/fail per test. Used in CI. Provides confidence that the filters behave as documented.

**`distill gain` with confidence indication**
`distill gain` shows: "Estimated savings: 2.3M tokens (±20%, based on char/4 approximation)." The uncertainty is explicit. If Claude API token counting is available via `ANTHROPIC_API_KEY`, show "Verified savings: 1.9M tokens." Users know what they're getting.

---

**Tier 2: High value post-launch**

**`distill doctor`**
Health check for the entire installation:
- Hook is installed and responding correctly? ✓
- Built-in filters all passing inline tests? ✓
- Project-local filter trusted and hash current? ✓
- User-global filter file valid TOML? ✓
- SQLite database accessible and schema current? ✓
- Any filters that haven't fired in 30 days (possibly dead)? ⚠ 3 filters
- Any filters with >10% `on_empty` trigger rate (possibly too aggressive)? ⚠ 1 filter
`distill doctor --fix` resolves what can be auto-fixed.

**`distill audit [command]`**
Full audit log of what Distill has done:
`distill audit pytest` shows every time pytest was filtered in the last 90 days, with timestamp, project, tokens before, tokens after, and which stages fired. Lets users verify empirically that filtering is working and not losing critical content.

**`distill trust --diff <path>`**
Before re-trusting a changed project-local filter: shows exactly what changed in the filter file since it was last trusted. Not just "the file changed" — a contextual diff of the TOML content. Users make informed trust decisions.

**`distill simulate <input-file> --filter <name>`**
Given a file containing sample command output, shows how the named filter transforms it. Used when writing new filters. Eliminates the need to run a real command just to test a filter pattern.

---

**Tier 3: Competitive differentiators**

**`distill benchmark <command>`**
Runs the command N times (configurable), applies filtering each time, reports: median latency, p99 latency, median token reduction, consistency of reduction. Used to evaluate whether a filter performs reliably across different command outputs.

**`distill diff --filter <name> v1 v2`**
Given two versions of a filter definition, shows how the filtering behavior changes on a set of sample inputs. Invaluable for maintaining filters as tools update their output formats.

**`distill log [--today|--project <path>]`**
Chronological log of AI activity filtered by Distill. What commands were run, when, what was filtered, what was passed through. The AI's activity through Distill's eyes. Useful for debugging AI sessions that produced unexpected results.

**`distill off` / `distill on`**
Temporarily disable all filtering without uninstalling the hook. The hook still intercepts commands but returns them unchanged. Allows quick A/B testing: "Let me run this session without filtering and compare."

---

## 7. Measuring Success

### KPIs We Can Measure Honestly

**Primary: Token Reduction**
- `median_tokens_saved_per_command`: Median (not mean — resistant to outliers from very large outputs)
- `filter_hit_rate`: % of AI-issued commands that were filtered (higher = better coverage)
- `passthrough_rate`: % of commands that had no matching filter (inverse of above)
- `stage_utilization_rate`: Per stage per filter, % of invocations where that stage actually reduced output. A stage with 0% utilization is dead code.

**Secondary: Coverage Quality**
- `on_empty_trigger_rate`: % of filtered commands where output was reduced to empty and `on_empty` fired. >10% for any filter indicates the filter is too aggressive.
- `unfiltered_command_frequency`: Which commands appear most often in the passthrough log? Drives roadmap prioritization.
- `unique_commands_seen`: Total unique command patterns the AI ran. Reveals coverage gaps.

**Tertiary: System Health**
- `filter_latency_p50_p99_ms`: Processing time per filter invocation. `p99 > 200ms` is a problem.
- `hook_error_rate`: % of hook invocations that failed and resulted in passthrough due to error. Should be zero.
- `trust_rejection_rate`: % of project-local filter loads that were blocked due to hash mismatch.

**Proxy for AI Quality (honest about limitations)**
- `command_rerun_interval_seconds`: If the AI runs the same command twice within N seconds, the first run was probably insufficiently detailed. This is a PROXY — not direct evidence. Log it; surface it in `distill audit`.
- `session_depth`: How many distinct commands does an AI session run before completing a task? Lower may indicate better context utilization. Extremely noisy metric.

### Metrics We Must NOT Report

- **Theoretical maximum compression**: The difference between raw output and the theoretical minimum. Misleading — some content must be retained.
- **GitHub stars**: Vanity metric. A popular but ineffective tool is worse than an effective but obscure one.
- **Total tokens "saved" globally**: We cannot aggregate across users without opt-in telemetry. Do not report invented numbers.
- **"X% faster AI responses"**: We cannot prove this. Latency depends on model, context, and hardware. Never claim it.

---

## 8. Benchmark Suite Design

The benchmark suite serves two purposes: (1) regression testing across Distill versions, and (2) honest comparison with Zap. All benchmark inputs must be realistic (from real command outputs), not synthetic.

### Benchmark Structure

Each benchmark item is a tuple: `(command, sample_output_file, expected_filter_name, minimum_reduction_pct, must_preserve_patterns)`.

`must_preserve_patterns`: regex patterns that MUST appear in the filtered output. If any are missing, the benchmark fails regardless of token savings.

### Category 1: Documentation Prompts
*AI reading project documentation, man pages, help output*

| ID | Command | Input Lines | Min Reduction | Must Preserve |
|---|---|---|---|---|
| DOC-01 | `git log --oneline -50` | ~50 | 30% | commit hashes, message keywords |
| DOC-02 | `pip show requests` | ~15 | 20% | version, location |
| DOC-03 | `cargo metadata --format-version 1` | 200+ | 60% | package names, versions |
| DOC-04 | `kubectl explain pod.spec` | 100+ | 40% | field names, types |
| DOC-05 | `terraform providers` | 30+ | 20% | provider names |

### Category 2: Architecture Prompts
*AI understanding project structure*

| ID | Command | Input Lines | Min Reduction | Must Preserve |
|---|---|---|---|---|
| ARCH-01 | `find . -type f -name "*.py"` | 200+ | 40% | .py extension, relative paths |
| ARCH-02 | `tree src/` (3 levels deep) | 150+ | 30% | directory structure |
| ARCH-03 | `git log --graph --oneline -30` | ~60 | 25% | branch labels, merge commits |
| ARCH-04 | `ls -la src/` | 30+ | 40% | filenames, sizes |
| ARCH-05 | `cat pyproject.toml` | 50+ | 20% | [dependencies], [tool] sections |

### Category 3: Debugging Prompts
*AI diagnosing build failures, test failures, runtime errors*

| ID | Command | Input Lines | Min Reduction | Must Preserve |
|---|---|---|---|---|
| DEBUG-01 | `pytest tests/ -x` (3 failures, 50 passing) | 300+ | **70%** | FAILED lines, error messages, assertion details |
| DEBUG-02 | `cargo test` (5 failures) | 500+ | **80%** | test names, panic messages, line numbers |
| DEBUG-03 | `mypy src/` (10 errors) | 50+ | 40% | file:line:col, error text |
| DEBUG-04 | `tsc --noEmit` (5 errors) | 60+ | 40% | file:line:col, error text |
| DEBUG-05 | `ruff check .` (15 violations) | 40+ | 20% | file:line, rule ID |

DEBUG category has the highest `min_reduction` and the most stringent `must_preserve`. This is the highest-risk category — filtering test failures is where we're most likely to remove something the AI needed.

### Category 4: Coding Prompts
*AI during active development (running tests, checking types)*

| ID | Command | Input Lines | Min Reduction | Must Preserve |
|---|---|---|---|---|
| CODE-01 | `cargo check` (success) | 100+ | **85%** | "Finished" line |
| CODE-02 | `git status` (5 modified, 3 untracked) | 25+ | 40% | modified filenames |
| CODE-03 | `git diff HEAD src/main.py` | 80+ | 30% | +/- diff lines, function context |
| CODE-04 | `make` (success, entering/leaving dirs) | 80+ | **70%** | actual build commands |
| CODE-05 | `docker compose ps` (3 services) | 20+ | 30% | service names, status |

### Category 5: Product Management Prompts
*AI reviewing project health, release status*

| ID | Command | Input Lines | Min Reduction | Must Preserve |
|---|---|---|---|---|
| PM-01 | `gh pr list --limit 20` | 25+ | 30% | PR numbers, titles, status |
| PM-02 | `gh issue list --label bug --limit 15` | 20+ | 30% | issue numbers, titles |
| PM-03 | `terraform plan` (2 changes) | 80+ | **75%** | resource names, change type |
| PM-04 | `git log --since=1week --oneline` | 30+ | 20% | commit messages |
| PM-05 | `pip list --outdated` | 20+ | 20% | package names, versions |

### Category 6: Mixed Session Prompts
*Simulate a real AI coding session across multiple commands*

| ID | Sequence | Total Lines | Min Reduction |
|---|---|---|---|
| MIX-01 | git status → git diff → pytest | 400+ | 60% |
| MIX-02 | cargo check → cargo test (fail) → git log | 800+ | 70% |
| MIX-03 | find → cat several files → mypy | 600+ | 50% |
| MIX-04 | docker ps → kubectl get pods → terraform plan | 300+ | 65% |
| MIX-05 | npm install → tsc → eslint | 400+ | 60% |

### Benchmark Execution

```
distill benchmark run --suite all --output benchmark-results.json
distill benchmark compare v0.1.0 v0.2.0 --suite debugging
distill benchmark report --format markdown
```

The benchmark suite is committed to the repository as fixture files. Each release must publish benchmark results. A PR that regresses any benchmark by >5% requires explicit justification.

---

## 9. Version 1.0 Blind Spots — Missing Capabilities Users Will Immediately Request

Ranked by expected request volume and severity of absence.

**1. Credential / Secret Detection and Redaction** (Severity: High)

A developer asks the AI to run `git log -5 --stat`. One of those commits included an API key in a commit message. The AI now has the key in its context window (sent to Anthropic servers). Distill has no mechanism to detect or redact credentials.

This is not just a user feature request — it is a liability. We should have a basic secret pattern detector that either redacts or refuses to pass content matching `sk-...`, `ghp_...`, `AKIA...` patterns. Distill is not a DLP tool, but we should be conservative.

**2. Streaming / Live Feedback for Long Commands** (Severity: High)

A 60-second `cargo build --workspace` produces nothing for the AI during those 60 seconds. Users immediately want: "Show me the first error as it appears, don't wait for the whole build." This requires fundamental architecture changes (streaming subprocess output through the filter pipeline). We cannot retrofit this easily without planning for it now.

Recommendation: design the executor interface to SUPPORT streaming even if the v1 implementation is synchronous. `Executor.run(command) -> FilteredResult` vs. `Executor.stream(command) -> Iterator[FilteredLine]`. If we ship v1 with the blocking interface, streaming becomes a breaking change to the executor API.

**3. Windows PowerShell Command Translation** (Severity: High — critical for this user)

`git status 2>&1` is bash syntax. PowerShell has different redirect syntax. `cargo test 2>&1 | head -50` is not valid PowerShell. The hook needs to understand which shell is running and handle syntax translation accordingly.

This is specifically relevant to this project: the user is on Windows. Before v1.0, we must decide: do we handle PowerShell redirect syntax, or do we document that the hook only rewrites command names (not syntax) and the commands are passed to the same shell they were issued in?

**4. IDE Integration** (Severity: Medium)

Most AI coding assistant users don't use the terminal directly — they use VS Code, Cursor, or JetBrains IDE integrations. The hook works at the shell level, which means IDE-based AI assistants that use their own tool execution engine (not a shell) may not trigger the hook at all. 

Users will immediately ask: "Does this work with the VS Code Claude extension?" The answer may be "no" for some assistant modes.

**5. Multi-Model Token Budget Awareness** (Severity: Medium)

Claude 3.5 Sonnet has a 200K token context. GPT-4o has 128K. Gemini 1.5 has 1M. The "optimal" compression level is different for each model. A user switching between Claude Code and Cursor is getting the same filtering regardless of which model they're using. Users will ask for model-aware compression profiles.

**6. `distill off` per-session or per-project** (Severity: Medium)

The ability to disable filtering for a single session or project without reinstalling the hook. `DISTILL_DISABLED=1 cargo test` (per-command) exists in Zap. But `distill disable --project .` for a whole project doesn't.

**7. Regex Safety** (Severity: Medium — but technical debt)

Python's `re` module is vulnerable to catastrophic backtracking on pathological inputs. A TOML filter with a badly written regex applied to a large output could hang the hook for seconds. We will get bug reports about this. The mitigation (`re2` package or timeout wrapper) should be designed before v1.0, not after.

**8. Filter Conflict Detection** (Severity: Low-Medium)

If a user has `max_lines = 20` in their project-local filter AND the built-in filter has `max_lines = 100`, which wins? The current answer is: project-local overrides built-in (first-match wins in lookup). But what if the user INTENDED to extend the built-in, not replace it? Filter inheritance/extension is a missing concept.

**9. Configuration Validation** (Severity: Low)

`distill config --validate` that catches: invalid TOML syntax, unknown config keys, values out of valid range, mutually exclusive settings. Currently validation happens at load time, errors are swallowed, and defaults are used. Users get silent misconfiguration.

**10. Session Context Tracking** (Severity: Low but interesting)

"How much of my context window has been consumed in this AI session?" Users want a running total, not just a historical report. This requires integrating with the AI's context window usage API (Claude provides this in responses). Not trivial to implement but frequently requested.

---

## 10. Critical Review: Would You Approve Version 1.0?

**Panel verdict: No. Not yet.**

Here is every gap that must be resolved before public release, ranked by severity.

---

### Blocking Issues

**B1: The core hypothesis is untested and the documentation is silent about it.**

The analysis document states: "Zap asserts that compressed output is better for AI. This is a hypothesis, not a fact."

Distill inherits this exact problem. Version 1.0 will be released claiming to improve AI coding assistant performance, with zero empirical evidence. If a user discovers that Distill is silently removing error context that caused the AI to fail to fix their bug, we have shipped a product that actively harms users — but claimed to help them.

Resolution: The README must be honest. "Distill reduces token consumption. We believe this improves AI session quality by preserving context space, but we cannot currently measure AI task success rate directly. `distill gain` shows you exactly what was removed so you can verify." Include `distill preview` before v1.0 so users can audit their filtering. Do not claim improved AI quality without evidence.

**B2: The plugin API is not specified, but it must be stable at v1.0.**

The design review recommends an entry-points-based plugin system. The `FilterPlugin` Protocol is sketched in the analysis document. But the exact API — method signatures, argument types, return types, error behavior, lifecycle hooks — is not specified.

If we release v1.0 with even one community plugin, that plugin's author will depend on the API we shipped. Every subsequent breaking change destroys community trust.

Resolution: Finalize the plugin API in a design specification before writing any code. Treat it like a public REST API — breaking changes are major versions.

**B3: Windows path handling in GLOB queries is not designed.**

The analysis recommends `GLOB path/*` for project-scoped queries. On Windows, paths are `C:\Users\username\project`. A GLOB of `C:\Users\username\project\*` contains backslashes, which GLOB may interpret as escape characters. The analysis acknowledges this but defers the solution.

This is a concrete data corruption bug waiting to happen. Every project-scoped `distill gain` query on Windows will return wrong results if we don't solve this now.

Resolution: Decide and document: normalize all stored paths to forward-slash posix representation in SQLite. `Path.as_posix()` on insertion, GLOB with forward slashes. Test this on Windows before v1.0.

**B4: Regex catastrophic backtracking is unmitigated.**

User-defined TOML filters contain arbitrary regex patterns. Python's `re` module uses a backtracking NFA engine. A pathological pattern (`(a+)+$`) on a large output string can hang for seconds, minutes, or forever. This is not theoretical — it affects real regex patterns written by non-experts.

The Python `regex` package (drop-in replacement) uses a hybrid engine that falls back to linear-time matching for most inputs. Alternatively, we can wrap each stage execution in a `signal.alarm()` timeout (Unix only) or a thread with timeout (cross-platform but heavy).

Resolution: Use the `regex` package instead of `re` for all user-defined patterns. Document this choice. Add a note in the filter authoring guide about catastrophic backtracking patterns to avoid.

**B5: No error contract tests exist.**

The design states: "The hook adapter must never raise. If anything fails, log to stderr and return the original command unchanged." This is a critical correctness requirement. If the hook crashes — due to a JSON parse error, a regex error, an import error, or any other exception — the AI session breaks.

A contract without a test is a hope, not a guarantee.

Resolution: Before v1.0, write property-based tests (hypothesis) that feed arbitrary bytes to the hook adapter and verify it always returns bytes (never raises). Run these in CI.

---

### Pre-Release Requirements

**P1: `distill preview` must ship with v1.0, not after.**

It is acceptable to ship a simple implementation (text diff, not a rich UI). But some form of "show me what filtering did to this output" is table stakes for a tool that modifies data before the AI sees it.

**P2: `distill verify` must be a CI step from day one.**

Every built-in filter requires at least 2 inline tests. `distill verify` must pass in CI on every commit. The testing infrastructure must exist before the first filter is written.

**P3: Secret pattern detection must exist at v1.0, even if basic.**

A list of 20-30 known secret patterns (AWS access keys, GitHub tokens, Anthropic API keys, generic `sk-...` prefixes) with a warning (not a block — blocking would break sessions) when matched in command output. Log to stderr: "distill: potential secret pattern detected in output of `git log`. Review `distill audit git-log` to verify." The user can suppress with `DISTILL_IGNORE_SECRETS=1`.

**P4: The TOML filter schema must enforce mutual exclusivity of strip/keep.**

`strip_lines_matching` and `keep_lines_matching` are documented as mutually exclusive. Zap doesn't enforce this at parse time. We must. Load-time validation that raises a clear error if both are set.

**P5: Input size cap must be implemented before v1.0.**

An unbounded subprocess output going through the filter pipeline is a memory exhaustion vulnerability. Cap at 10MB (configurable). Above the cap: emit the first 5MB through the filter and append a truncation notice. Document this behavior.

**P6: The executor interface must support streaming (even if v1 is synchronous).**

Define `Executor.stream(command) -> Iterator[str]` in the interface today. Implement it as a synchronous-to-iterator adapter in v1. This preserves the ability to add true streaming in v2 without a breaking API change.

---

### Technical Debt We Can Avoid Today

**TD1: Don't use `ceil(len(text) / 4)` as the only estimation strategy.**

Make token estimation an injectable function from day one: `estimate_tokens: Callable[[str], int] = default_estimator`. The default is `ceil(len(text) / 4)`. Users with API access can inject the actual tokenizer. This is 5 lines of code to add now; retrofitting it later requires changing every call site.

**TD2: Add `is_passthrough` boolean to the tracking schema now.**

The zero-token sentinel (`tokens_before=0, tokens_after=0`) works but is semantic overloading. A boolean column `is_passthrough BOOLEAN DEFAULT 0` is clearer, enables better queries, and is impossible to accidentally produce from a real filtered command. Adding a column to SQLite after release requires a migration.

**TD3: Build-in filter files should have a schema version field.**

```toml
[meta]
schema_version = 1
distill_min_version = "0.1.0"
```

When we change the filter schema in v2 (and we will — every product does), filters without a version field will be ambiguous. Add the version field to every built-in filter from the start.

**TD4: Separate the classifier from the rule table.**

The classifier and the rule table should be in separate modules. The rule table (`rules.py`) is a data file. The classifier (`classifier.py`) is logic. They should be independently testable. If they're in one module, changing a rule requires reading classifier logic to understand the impact.

**TD5: Document the "last match wins" invariant in the rule table file.**

```python
# rules.py
# ORDERING INVARIANT: Rules are ordered general-to-specific.
# When multiple rules match a command, the LAST match wins.
# More specific rules MUST appear AFTER more general rules.
# Violating this invariant produces incorrect classification silently.
RULES = [...]
```

This invariant is architectural. It will be violated by contributors who add rules at the end of the file without understanding why order matters. The comment prevents the bug.

---

### Summary Table

| Issue | Severity | Blocks v1.0? |
|---|---|---|
| Core hypothesis not validated; documentation silent | Critical | Must address (in docs) |
| Plugin API not specified before code is written | Critical | Yes |
| Windows path GLOB handling not designed | High | Yes |
| Regex catastrophic backtracking unmitigated | High | Yes |
| No error contract tests | High | Yes |
| `distill preview` missing | High | Yes |
| `distill verify` not in CI | High | Yes |
| Basic secret detection missing | Medium | Yes |
| strip/keep mutual exclusion not validated | Medium | Yes |
| Input size cap missing | Medium | Yes |
| Streaming interface not future-proofed | Medium | Must address (in design) |
| Token estimator not injectable | Low | No (technical debt) |
| `is_passthrough` column missing | Low | No (technical debt) |
| Filter schema version missing | Low | No (technical debt) |
| Classifier/rule table not separated | Low | No (technical debt) |
| Rule ordering invariant not documented | Low | No (technical debt) |

**Final verdict**: Version 1.0 is achievable, but it requires resolving 5 blocking technical issues and 6 pre-release requirements before the first public commit. The technical debt items can follow in v1.1, but they should be tracked as GitHub issues from day one so they don't get forgotten.

The product concept is sound. The architecture is defensible. The risks are manageable with upfront design work. The biggest risk is not technical — it is shipping a tool that claims to improve AI quality without being able to prove it. Transparency about what Distill does and does not claim is as important as the engineering.
