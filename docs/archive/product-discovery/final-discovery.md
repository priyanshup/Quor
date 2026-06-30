# Distill — Final Design Discovery
## Pre-Implementation Panel Review

> Panel: Principal Software Architect · Compiler Engineer · Information Retrieval Expert · NLP Researcher · Prompt Engineering Expert · Claude Code Expert · Open Source Maintainer · Product Architect
> Status: Final research phase before implementation begins.
> Every technique is labelled: Proven / Industry Practice / Promising / Speculative.

---

## 1. User Journey

The complete experience from zero to contributor.

---

### 1.1 Discovery

**Channels (ranked by realistic volume):**
1. A blog post or tweet showing a `distill gain` screenshot ("saved 4.2M tokens this month")
2. Claude Code forum or Discord — someone asking "how do I reduce context consumption?"
3. GitHub search for "claude code hooks" or "prompt optimization"
4. Word of mouth from a colleague who installed it
5. Anthropic's own documentation on hooks (if Distill is featured there)

**Friction at discovery:**
- The name "Distill" competes with `distilbert` and `whisper distill` in search. Consider a more unique name or domain.
- The README must answer "what does this actually do?" in 30 seconds. The Zap README takes 2 paragraphs to get to a concrete example. Distill must open with a `distill gain` screenshot and a before/after filter example.
- The README must immediately state: "Works on Windows. Pure Python. No Rust required." — these are the exact differentiators from Zap.

**Improvement:** The README should include a reproducible demo: copy-paste this one command, run this one AI session, see this result. Verifiable within 5 minutes.

---

### 1.2 Installation

**Target flow:**
```
pip install distill-ai
distill --version
```

**What must work on first run:**
- `pip install` succeeds without compiling anything
- `distill --version` prints version and exit code 0
- `distill doctor` (no hook installed yet) prints a clear next step

**Friction points:**
- Package name: `distill` is taken on PyPI (it is a generic name). Reserve a namespace: `distill-ai`, `distill-ctx`, or `distillcc`. Decide this before any code is written.
- Corporate proxy: `pip install` behind a proxy may fail silently. Add a `distill doctor --connectivity` check.
- Python version: must clearly require 3.11+ (for `tomllib`). The `pip` error message for wrong Python version is confusing. Detect and print a clear message.
- PATH: on Windows, `pip install --user` installs to a path not always in `%PATH%`. The post-install message must explain this.
- Virtual environments: if installed inside a venv, the hook script must invoke the venv's Python, not the system Python. This is a concrete bug if not designed for.

**Improvement — zero-friction install check:**
After `pip install`, immediately run a self-test: if all basic imports succeed, print "✓ Distill installed correctly. Run `distill init --claude` to set up the hook." If any import fails, print exactly what failed and why.

---

### 1.3 Hook Registration

**Target flow:**
```
distill init --claude
```

**What this must do:**
1. Locate Claude Code's settings file (`~/.claude/settings.json` on Unix, `%USERPROFILE%\.claude\settings.json` on Windows)
2. Show the user what will be added (dry-run output, not just "done")
3. Make the change atomically (write to temp file, rename)
4. Confirm success: "Hook registered. Run `distill hook claude --test` to verify it works."

**Friction points:**
- If settings.json doesn't exist yet (user hasn't run Claude Code): create it or print instructions
- If settings.json is not valid JSON (corrupted): detect and abort with a clear error, never corrupt further
- Multiple Claude Code versions may store settings in different locations. `distill init` must handle both.
- On Windows, `%USERPROFILE%` path with spaces requires quoting in the hook script. Test this explicitly.
- **The venv problem (critical):** The hook script calls `python -m distill hook claude`. If `python` resolves to the system Python (not the venv where Distill is installed), the hook silently fails. The `distill init` command must embed the FULL path to the Python executable, not `python` or `python3`.

**Improvement — verified registration:**
After registration, immediately run `distill hook claude --test` automatically. Send a synthetic hook request and verify the response is correct. Print pass/fail. Users should not discover hook failures at session time.

---

### 1.4 First Optimization

**The moment that converts a user into a believer.**

The first time the AI runs `git status` and the hook rewrites it, the user sees nothing different. This is by design — but it means there is no "first optimization" moment unless we create one.

**Recommendation: an onboarding mode.**

After hook installation, for the first 5 filtered commands, print to stderr (not stdout — AI doesn't see stderr):
```
[distill] git status → distill git status (hook active)
[distill] Estimated: 847 tokens → 203 tokens (76% reduction)
[distill] Run `distill gain` to see cumulative savings.
```

After 5 commands, switch to silent mode. This gives users the "aha" moment without permanent noise.

**Alternative:** A `distill watch` command that streams a live log of what the hook is doing, in a separate terminal. Developers can run this during their first AI session to verify everything is working.

---

### 1.5 Daily Usage

**The ideal state: invisible.** The developer installs Distill and forgets it. It runs transparently.

**Daily friction points:**
- When a command isn't filtered (because no rule matches), the AI gets unfiltered output. The developer has no way to know this happened or why. `distill gain --today` should show a "top unfiltered commands" section.
- When a filter is too aggressive (AI re-runs a command asking for more detail), the developer has no way to connect this to Distill's filtering. The re-run tracking proxy metric (described in design-review.md) addresses this.
- When Claude Code is updated and changes its hook format, the hook breaks silently. `distill doctor` must detect this via a synthetic hook test, not just by checking file existence.

---

### 1.6 Configuration

**Target state:** most users never touch configuration. Defaults are correct.

**When users reach for config:**
1. A command they use frequently isn't being filtered (`exclude_commands` or missing rule)
2. A filter is too aggressive (they need to see more output)
3. They use Docker/poetry wrapper commands (`transparent_prefixes`)
4. They want a custom filter for an internal tool

**Configuration UX:**
- `distill config show` — readable TOML with comments explaining each field
- `distill config edit` — opens config in `$EDITOR`
- `distill config set hooks.transparent_prefixes="docker exec mycontainer"` — single-field update without opening an editor
- `distill config validate` — checks for syntax errors and unknown keys before they cause silent failures

**Friction:** TOML is the right format but has a learning curve for non-Python users. Every config field must be documented inline as a TOML comment in the default config file.

---

### 1.7 Statistics

**`distill gain` is the primary analytics command. It must earn its screen time.**

Levels of depth:
```
distill gain                    # Summary for current project
distill gain --global           # All projects, last 90 days
distill gain --today            # Today only (motivating)
distill gain --filter pytest    # Single filter performance
distill gain --unfiltered       # Top commands without a filter (roadmap input)
distill gain --json             # Machine-readable for dashboards
```

**The statistics must be honest about uncertainty.** "Estimated: 2.3M tokens (±20% — based on char/4 approximation)." If the user has `ANTHROPIC_API_KEY` set, offer to compute exact counts.

**Improvement — session attribution:**
When the AI session ends (detectable via Claude Code's PostSession hook if one exists), summarize: "This session: 47 commands, 23 filtered, ~18,400 tokens saved." The cumulative number is good; the per-session number is motivating.

---

### 1.8 Troubleshooting

**`distill doctor` is the troubleshooting entry point.** It must cover:

1. Hook installed and currently responding? (Run a synthetic hook call)
2. All built-in filters passing inline tests?
3. Python executable in hook script matches installed Distill?
4. SQLite database readable and schema current?
5. Any filters with hash mismatch (project-local trust)?
6. Any filters with >15% `on_empty` trigger rate (possibly over-aggressive)?
7. Any commands appearing in passthrough log >20 times today (coverage gap)?

**`distill explain <command>`** for per-command investigation:
```
distill explain "pytest tests/ -x"
→ Matched filter: pytest (built-in, v0.3.1)
→ Stage 1 strip_ansi: 0 chars removed (no ANSI codes)
→ Stage 4 strip_lines_matching: 142 lines removed (patterns: PASSED, ^\s*$)
→ Stage 7 max_lines: not triggered (38 lines remain, limit 100)
→ Result: 178 lines → 38 lines, 1,240 tokens → 261 tokens (79% reduction)
→ Preview: distill preview pytest tests/ -x
```

**Friction:** When the hook fails silently (Python path wrong, import error), there is nothing in the AI session to indicate the problem. `distill doctor --test-hook` must expose this. The hook should write a structured log to `~/.local/share/distill/hook.log` (not just stderr) so post-hoc debugging is possible.

---

### 1.9 Updating

```
pip install --upgrade distill-ai
distill doctor  # verify nothing broke
```

**What must happen automatically on update:**
- SQLite schema migrations (if schema changed)
- Built-in filter updates (new version, better patterns)
- Hook script re-check (hook format may have changed)

**What must NOT happen automatically:**
- Changing user-modified config
- Re-writing project-local filters
- Changing the hook if the user customized it

**Improvement — `distill changelog`:** Shows what changed between the installed version and the latest. Focused on: new filters added, filter improvements, breaking changes. Developers want to know if a filter they depend on changed.

---

### 1.10 Contributing

**The primary contribution path: adding a TOML filter.**

Target UX:
```
distill new-filter terraform-apply
→ Creates: ~/.config/distill/filters/terraform-apply.toml (template)
→ Opens in $EDITOR
→ After saving: distill verify terraform-apply (run inline tests)
→ Satisfied: distill contribute terraform-apply (opens GitHub PR template)
```

**Friction in contributing:**
- "How do I know what pattern to use?" — `distill simulate "terraform apply" --capture` runs the real command and saves output to a file that can be used as a filter test fixture
- "How do I know my filter is good enough?" — `distill verify` plus a minimum test count requirement (≥3 inline tests) enforced by CI
- "How do I submit?" — the `distill contribute` command generates a PR description template with: what the filter does, what it strips, the inline test coverage

**For Python filter plugins:**
- Template repository: `distill-plugin-template` on GitHub
- `cookiecutter distill-plugin-template` generates a properly structured package
- Plugin publishing guide in docs

---

### 1.11 Uninstalling

```
distill uninstall --hook-only    # Remove hook, keep config and data
distill uninstall                # Remove hook and config (keeps tracking data)
distill uninstall --all          # Remove everything including tracking database
```

**What must be reversible:** Everything. The hook installation is reversible (remove the JSON entry). The config is reversible (delete the file). The tracking database is reversible (delete the SQLite file).

**What must never happen:** Uninstalling Distill must not corrupt Claude Code's settings.json. Atomic removal: read the JSON, remove the hook entry, write back atomically.

---

## 2. Complete Optimization Landscape

Categorized, with honest maturity assessments.

---

### Category A: Lexical (Character and Token Level)

**A1. Whitespace Normalization**
- Purpose: Remove redundant blank lines, trailing whitespace, mixed indent
- Token savings: 2–8%
- Risk: Very low (whitespace is semantic only in Python source — never apply to code)
- Computational cost: Trivial (single-pass string scan)
- Complexity: Trivial
- Maturity: Production standard
- Adoption: Universal in text processing
- *Label: Industry Practice*

**A2. ANSI/Terminal Escape Code Stripping**
- Purpose: Remove color codes, cursor movement, clear-screen sequences that add token overhead with zero semantic value
- Token savings: 5–15% for colorized tool output
- Risk: None (purely cosmetic content)
- Computational cost: Low (regex, single pass)
- Complexity: Low
- Maturity: Production standard
- Adoption: Zap, every terminal output processor
- *Label: Proven*

**A3. Duplicate Line Removal (Exact)**
- Purpose: Remove exact-duplicate lines from output (e.g., repeated warning messages)
- Token savings: 5–40% for tools with repeated diagnostics
- Risk: Low (identical lines carry no additional information)
- Computational cost: Low (set-based deduplication, O(n))
- Complexity: Low
- Maturity: Production standard
- Adoption: Log aggregation tools (Splunk, Datadog), test runners
- *Label: Industry Practice*
- **Note: Zap does NOT implement this. It should be in Distill.**

**A4. Near-Duplicate Line Removal**
- Purpose: Remove lines that differ only in variable content (timestamps, PIDs, line numbers)
- Token savings: 10–30% for log output with timestamp noise
- Risk: Medium (normalized form may lose timing information the AI needs)
- Computational cost: Medium (requires similarity comparison or regex normalization)
- Complexity: Medium
- Maturity: Production (used in log deduplication tools)
- Adoption: Sentry, Bugsnag (event grouping), logfmt processors
- *Label: Industry Practice*

**A5. URL Normalization / Truncation**
- Purpose: Long URLs add tokens with low semantic density; shorten or normalize
- Token savings: 1–5% when URLs are present
- Risk: Low if URLs are preserved in recognizable form (e.g., keep domain only)
- Computational cost: Trivial
- Complexity: Low
- Maturity: Production (link shorteners, log processors)
- Adoption: Limited in prompt optimization specifically
- *Label: Industry Practice*

**A6. Hash Truncation**
- Purpose: SHA-256 hashes in output (git commit hashes, file checksums) are 64 chars but 8 is enough for identification
- Token savings: 1–3% in git-heavy output
- Risk: Very low (short hashes are standard in git — `git log --abbrev-commit`)
- Computational cost: Trivial (regex replace)
- Complexity: Low
- Maturity: Production (git does this natively)
- Adoption: Zap does NOT implement this. Worth adding for git output.
- *Label: Industry Practice*

**A7. Number Precision Normalization**
- Purpose: Floating-point numbers with 8 decimal places in profiling output add tokens; 2 significant figures is usually enough
- Token savings: <1% in most contexts, higher in profiling output
- Risk: Low for display numbers; HIGH if the number is a measurement the AI might act on
- Computational cost: Trivial
- Complexity: Low
- Maturity: Standard in numeric formatting
- Adoption: Very limited in prompt optimization
- *Label: Promising* (narrow use case)

**A8. Timestamp Normalization**
- Purpose: Replace precise timestamps ("2026-06-30T14:23:41.847Z") with relative ("2 minutes ago") or remove them
- Token savings: 2–5% in log output
- Risk: Medium (absolute timestamps may be contextually relevant)
- Computational cost: Low
- Complexity: Low
- Maturity: Production (log formatters)
- Adoption: Zap does NOT implement. Valuable for docker logs, cloud CLI output.
- *Label: Industry Practice*

---

### Category B: Structural (Document and Section Level)

**B1. Head / Tail Selection**
- Purpose: Keep first N and/or last M lines
- Token savings: 50–95% on outputs where signal is concentrated at start or end
- Risk: Medium (middle content lost; correct only when start/end contains the signal)
- Computational cost: Trivial (list slice)
- Complexity: Low
- Maturity: Production (Zap, universal)
- Adoption: Ubiquitous
- *Label: Proven*

**B2. Absolute Line Count Cap**
- Purpose: Hard upper bound on output lines
- Token savings: Highly variable (0% if output is already short, 90%+ if output is huge)
- Risk: Low as a safety net; medium as a primary strategy
- Computational cost: Trivial
- Complexity: Trivial
- Maturity: Production (Zap, universal)
- *Label: Proven*

**B3. Section Extraction**
- Purpose: For structured output (with section headers), keep headers and discard or summarize bodies
- Token savings: 30–70% for verbose structured output (kubectl describe, terraform plan details)
- Risk: Medium (bodies may contain the relevant detail)
- Computational cost: Low (line scan for header patterns)
- Complexity: Low–Medium
- Maturity: Production in documentation tools
- Adoption: Limited in prompt optimization specifically
- *Label: Promising*

**B4. Repetition Counting (N× pattern)**
- Purpose: "The same warning appears 200 times" → "warning: deprecated API (×200)"
- Token savings: 50–99% for tools that repeat the same message (npm audit, deprecation warnings)
- Risk: Low (repetition count preserved)
- Computational cost: Low (group-by before emitting)
- Complexity: Low–Medium
- Maturity: Production (test runners, log aggregators — Sentry's event grouping, pytest's `-q` mode)
- Adoption: Zap does NOT implement this. **High-value addition for Distill.**
- *Label: Proven (in adjacent domains)*

**B5. Progressive Disclosure Formatting**
- Purpose: Show summary with "N more items hidden. Use `distill expand` to see them."
- Token savings: 30–70%
- Risk: Low (summary preserved; details accessible on demand)
- Computational cost: Low
- Complexity: Medium (requires a way to store and retrieve the hidden content)
- Maturity: Production in UI (GitHub file diffs collapse large diffs)
- Adoption: Not in terminal prompt optimization
- *Label: Promising* (novel for this domain)

**B6. Table Deduplication / Compression**
- Purpose: Tabular output (docker ps, kubectl get pods) repeats column headers; dense tables can have columns the AI doesn't need
- Token savings: 5–20%
- Risk: Low for column selection; medium for row deduplication
- Computational cost: Low
- Complexity: Medium (requires table format detection)
- Maturity: Production in data processing
- Adoption: Zap handles this implicitly via strip_lines_matching but not structurally
- *Label: Industry Practice*

---

### Category C: Syntax-Aware (Language and Format Specific)

**C1. Code Comment Stripping**
- Purpose: Comments are written for humans, not for AI understanding code structure
- Token savings: 15–40% in comment-heavy code files
- Risk: Medium (comments sometimes explain non-obvious logic the AI needs; doc comments are particularly valuable)
- Computational cost: Low (language-specific regex; tree-sitter for full accuracy)
- Complexity: Medium (must be language-aware; wrong comment detection corrupts code)
- Maturity: Production (Zap, code minifiers)
- Adoption: Widespread in code processing tools
- *Label: Proven* (with the caveat: never apply to data formats)

**C2. Import / Signature Extraction (Aggressive Code Filter)**
- Purpose: For large files, keep only the API surface (imports, function signatures, type definitions) and strip implementations
- Token savings: 40–80% for large implementation files
- Risk: High (AI may need implementation details to understand bugs or write correct code)
- Computational cost: Medium
- Complexity: High (requires reliable language parsing; regex is fragile for this)
- Maturity: Production in Zap; research in code summarization
- Adoption: Limited
- *Label: Industry Practice* (Zap), though the regex approach is fragile

**C3. AST-Based Structural Extraction**
- Purpose: Use tree-sitter to parse source code and extract precise structural elements
- Token savings: 50–80% vs. full file
- Risk: Medium (more reliable than regex but still drops implementation)
- Computational cost: Medium-High (tree-sitter parsing per file)
- Complexity: High (tree-sitter integration, language grammar maintenance)
- Maturity: tree-sitter itself is production (used in Neovim, GitHub, VS Code); its use for prompt compression is newer
- Adoption: Aider uses tree-sitter for context management
- *Label: Proven (tree-sitter); Promising (for prompt compression)*

**C4. JSON / YAML Structural Compression**
- Purpose: Remove null fields, empty arrays, redundant metadata from API/config output
- Token savings: 20–60% for verbose API responses (kubernetes YAML, terraform state)
- Risk: Medium (null fields may be semantically significant; depends on context)
- Computational cost: Low (parse + filter + serialize)
- Complexity: Medium (requires format detection and schema awareness)
- Maturity: Production in API middleware, data pipelines
- Adoption: Not implemented in Zap. Highly valuable for cloud CLI output.
- *Label: Industry Practice*

**C5. Stack Trace Compression**
- Purpose: Stack traces repeat framework frames (Django, Flask, pytest internals); keep only user-code frames
- Token savings: 50–80% for deep framework stack traces
- Risk: Low (framework frames are rarely relevant to the bug)
- Computational cost: Low (pattern match against known framework prefixes)
- Complexity: Low–Medium
- Maturity: Production (Sentry, Bugsnag, pytest's `--tb=short` mode)
- Adoption: Zap does NOT implement. **Highly valuable for Distill's Python target audience.**
- *Label: Proven (in error tracking systems)*

**C6. Diff Context Reduction**
- Purpose: `git diff` by default shows 3 context lines around each change; AI needs fewer
- Token savings: 20–50% depending on diff density
- Risk: Low (context lines provide surrounding code; 1–2 is often sufficient for AI)
- Computational cost: Trivial (count lines in unified diff format)
- Complexity: Low
- Maturity: Production (git's `--unified=N` flag; patch format is standardized)
- Adoption: Not in Zap explicitly (handled by max_lines). Could be a dedicated diff-aware stage.
- *Label: Industry Practice*

**C7. Log Level Filtering**
- Purpose: From mixed-level log output, keep only ERROR/WARN/CRITICAL lines
- Token savings: 60–90% for verbose application logs
- Risk: Low for diagnosis; medium if INFO context is needed to understand the error
- Computational cost: Low (pattern match on log level prefix)
- Complexity: Low (but must handle multiple log formats: logfmt, JSON logs, traditional)
- Maturity: Production (every log aggregation tool: Splunk, ELK, Loki)
- Adoption: Zap does NOT implement explicitly (some filters do this via strip_lines_matching)
- *Label: Proven*

---

### Category D: Semantic (Meaning-Level)

**D1. Extractive Summarization (Sentence Selection)**
- Purpose: Select the N most informative sentences from a passage using statistical relevance (TF-IDF, BM25, TextRank)
- Token savings: 50–80%
- Risk: High (statistical relevance ≠ task relevance; wrong sentences selected loses critical info)
- Computational cost: Medium (TF-IDF is fast; TextRank is slower)
- Complexity: High (requires NLP pipeline)
- Maturity: Proven in document summarization research (pre-2020)
- Adoption: LangChain's `ContextualCompressionRetriever`, LlamaIndex's `SentenceWindowNodeParser`
- *Label: Proven in adjacent domain; Promising for prompt compression*

**D2. LLM-Based Compression (LLMLingua approach)**
- Purpose: Use a small LM to estimate the "importance" of each token in the context, then prune low-importance tokens
- Token savings: 2x–20x (research claims)
- Risk: Medium (importance is estimated, not known; may prune tokens the larger model would have attended to)
- Computational cost: High (requires running a smaller LM per compression)
- Complexity: Very High
- Maturity: Academic — published 2023 (Pan et al., Microsoft Research, "LLMLingua: Compressing Prompts for Accelerated Inference of Large Language Models")
- Adoption: Limited production adoption; LlamaIndex has an experimental integration
- *Label: Academic Evidence* — numbers are from controlled benchmarks, not production use
- **Distill should NOT implement this in v1 or v2.** The latency cost (running a second LM per command) is incompatible with Distill's transparency philosophy. Log as a research track.

**D3. Perplexity-Guided Token Pruning**
- Purpose: Tokens with high perplexity (surprising given context) carry more information; low-perplexity (predictable) tokens can be pruned
- Token savings: Claimed 20–50% in research
- Risk: High (perplexity is a property of the model, not of the information; high-perplexity tokens may be typos, not important content)
- Computational cost: Very High (requires LM forward pass per token)
- Complexity: Very High
- Maturity: Academic (LLMLingua-2, 2024)
- Adoption: Essentially zero in production systems as of mid-2026
- *Label: Academic Evidence* (benchmark-only)
- **Distill: Research track only.**

**D4. Relevance-Based Filtering**
- Purpose: Given the current task or conversation context, filter output to only what's relevant
- Token savings: 40–80%
- Risk: Very High (relevance is subjective and task-dependent; wrong filtering causes AI failure)
- Computational cost: High (requires embedding computation or LM call)
- Complexity: Very High
- Maturity: Research stage (Selective Context, Li et al., 2023)
- Adoption: LangChain's document compressors (experimental)
- *Label: Academic Evidence*
- **Distill: Research track. Cannot be implemented reliably without knowing the AI's current task, which requires reading the conversation context.**

**D5. Semantic Deduplication**
- Purpose: Remove sentences/blocks that are semantically similar to content already seen, using embedding cosine similarity
- Token savings: 10–30% for verbose outputs with paraphrased repetition
- Risk: Medium (semantic similarity threshold requires careful tuning)
- Computational cost: Medium-High (embedding computation per sentence)
- Complexity: High
- Maturity: Production in RAG systems (deduplication before retrieval)
- Adoption: LlamaIndex, Chroma's deduplication features
- *Label: Industry Practice (in RAG); Promising for prompt compression*
- **Distill: V2 candidate, with embedding model as optional dependency.**

---

### Category E: Domain-Specific

**E1. Test Runner Failure Extraction**
- Purpose: From mixed pass/fail test output, extract only failures with full context
- Token savings: 70–95% on test suites with many passing tests
- Risk: Low (passing tests carry no new information for debugging)
- Computational cost: Low (pattern matching against known test runner formats)
- Complexity: Medium (different format per runner: pytest, Jest, cargo test, go test)
- Maturity: Production (Zap implements this, CI systems implement this)
- *Label: Proven*

**E2. Build Error Extraction**
- Purpose: From verbose build output, extract only error and warning lines
- Token savings: 60–90% for large builds
- Risk: Low–Medium (some "info" lines provide context for errors)
- Computational cost: Low
- Complexity: Low
- Maturity: Production
- *Label: Proven*

**E3. Git History Compression**
- Purpose: git log is extremely verbose by default; short format, subject-only, deduplication
- Token savings: 50–80%
- Risk: Low (full hash, author, timestamp rarely needed by AI; Abbrev hash is standard)
- Computational cost: Trivial
- Complexity: Low
- Maturity: Production (Zap, `--oneline` flag)
- *Label: Proven*

**E4. Cloud Resource State Diffing**
- Purpose: `kubectl get all` or `terraform show` lists all resources; AI usually only needs resources that changed
- Token savings: 70–90% in stable environments
- Risk: Medium (requires tracking previous state to compute diff)
- Computational cost: Medium (requires state persistence between invocations)
- Complexity: High (state management, diff computation)
- Maturity: Limited (terraform plan does this natively; kubectl diff is available)
- *Label: Promising* (architecturally complex for Distill)

**E5. Package Manager Output Compression**
- Purpose: `npm install` / `pip install` output contains progress bars, download speeds, checksums — none relevant to AI
- Token savings: 80–95%
- Risk: Low (keep: installed versions, warnings. Strip: progress, checksums, speeds)
- Computational cost: Low
- Complexity: Low
- Maturity: Production
- *Label: Proven*

---

### Category F: Safety

**F1. Credential / Secret Detection and Redaction**
- Purpose: Prevent API keys, tokens, passwords, private keys from entering AI context (sent to external model API)
- Token savings: Minimal (not a compression technique; a safety technique)
- Risk of NOT doing it: Very High
- Computational cost: Low (pattern matching against known secret formats)
- Complexity: Medium (many secret formats; false positive rate matters)
- Maturity: Production (GitHub Secret Scanning, AWS Macie, Trufflehog, detect-secrets)
- Adoption: Not in Zap. **Required for Distill before v1.0 release.**
- *Label: Proven (in secret scanning tools)*

**F2. PII Detection**
- Purpose: Prevent names, email addresses, phone numbers, IP addresses from entering AI context unnecessarily
- Token savings: Minimal
- Risk: Medium false positive rate for technical content (IP addresses in network output are often relevant)
- Computational cost: Low–Medium
- Complexity: High (PII is context-dependent)
- Maturity: Production (AWS Comprehend, Microsoft Presidio, spaCy NER)
- Adoption: Not in Zap.
- *Label: Industry Practice* (but complex to do well)
- **Distill: Optional, v2. Default off. Too many false positives in technical output.**

**F3. Entropy-Based Secret Detection**
- Purpose: High-entropy strings (base64, hex) that are unusually long may be secrets
- Token savings: Minimal
- Risk: Medium false positive (git hashes, UUIDs, encoded data are high-entropy but not secrets)
- Computational cost: Low (Shannon entropy calculation)
- Complexity: Low
- Maturity: Production (Trufflehog uses this)
- Adoption: Not in Zap.
- *Label: Industry Practice* (with known false positive problem)
- **Distill: V1 with conservative thresholds. Only flag, never block.**

---

### Category G: Context-Level (Beyond Individual Commands)

**G1. Conversation-Aware Deduplication**
- Purpose: If the AI already saw this file in the current session, don't re-send the full content
- Token savings: 30–60% for content that persists across commands
- Risk: Medium (file may have changed between reads)
- Computational cost: Low (hash of content → lookup in session cache)
- Complexity: High (requires reading the AI's conversation context)
- Maturity: Research stage; Anthropic's prompt caching addresses this at the API level
- Adoption: Not in Zap.
- *Label: Promising* (architecturally complex; requires hook access to conversation history)

**G2. Reference Compression**
- Purpose: If a long string (file path, error message, function name) appears multiple times across commands in a session, define it once and use a short alias thereafter
- Token savings: 10–30% across a long session
- Risk: Low (alias can be defined clearly)
- Computational cost: Low (session-level string tracking)
- Complexity: Medium (alias management, consistency)
- Maturity: Research stage for prompts; well-established in data compression (LZW)
- Adoption: Not in Zap.
- *Label: Promising* (novel application)

**G3. Context Budget Awareness**
- Purpose: As the context window fills, increase compression aggressiveness for subsequent commands
- Token savings: Highly situational; prevents context overflow
- Risk: High (adaptive aggressiveness can suddenly strip content that was preserved earlier)
- Computational cost: Low (if context usage is available via API)
- Complexity: High (requires knowing the AI's current context usage)
- Maturity: Research stage
- Adoption: Not in any tool.
- *Label: Speculative* (context usage not reliably accessible from hooks)

---

## 3. Zap Coverage Analysis

Every technique Zap uses, evaluated for Distill.

| Technique | Zap Implementation | Estimated Token Contribution | Risks | Distill Decision |
|---|---|---|---|---|
| ANSI stripping | `strip_ansi: true` in TOML | 5–15% | None | **Keep, identical** |
| Regex line stripping | `strip_lines_matching` | 30–80% (tool-dependent) | Pattern specificity | **Keep, improve validation** |
| Keep-only line filter | `keep_lines_matching` | 40–90% | Highest-risk stage | **Keep, add parse-time mutual exclusion validation** |
| Line count cap | `max_lines` | Safety net | None | **Keep, identical** |
| Pattern short-circuit | `match_output` + `unless` | 0% normally, 90%+ when fires | False positive collapse | **Keep, improve documentation of risk** |
| Regex substitution | `replace` with backrefs | 5–20% | Regex complexity | **Keep, add `regex` package for safety** |
| Head selection | `head_lines` | 20–80% | Middle content lost | **Keep, identical** |
| Tail selection | `tail_lines` | 20–80% | Middle content lost | **Keep, identical** |
| Line truncation | `truncate_lines_at` | 2–10% | Content truncated mid-line | **Keep, identical** |
| Empty fallback | `on_empty` | Behavioral, not token | None | **Keep, identical** |
| Stderr filtering | `filter_stderr` | Depends | Off by default correctly | **Keep, identical** |
| Minimal code filter | Strip comments, blank lines | 15–35% | Doc comments stripped | **Keep, improve doc-comment preservation** |
| Aggressive code filter | Signature extraction | 50–80% | High meaning risk | **Keep, redesign with tree-sitter for v2** |
| Command rewriting | `rewrite_command()` | Enables all above | Heredoc, pipe edge cases | **Keep, extend rule coverage** |
| Compound command splitting | `split_command_chain()` | Enables all above | Complex shell constructs | **Keep, identical semantics** |
| Transparent prefix stripping | `transparent_prefixes` | Enables above | Recursive depth limit | **Keep, identical** |
| Heredoc detection | `has_heredoc()` | Safety | False negative risk | **Keep, add here-string support** |
| Passthrough tracking | Zero-token recording | Analytics correctness | None | **Redesign: use `is_passthrough` boolean** |
| SHA-256 trust | Project-local filter security | Security | Usability friction | **Keep, improve UX** |
| SQLite tracking | WAL mode, GLOB scoping | Analytics | Platform, Windows paths | **Keep, fix Windows GLOB** |
| Build-time filter embedding | `build.rs` concat | Startup performance | Update requires rebuild | **Redesign: `importlib.resources`** |
| RegexSet dispatch | `lazy_static!` RegexSet | Performance | None | **Redesign: Python union regex with LRU cache** |
| 3-tier filter lookup | Project > user > built-in | Extensibility | Trust complexity | **Keep, add inheritance** |
| Inline TOML tests | `[[tests.filter]]` | Quality assurance | None | **Keep, identical format** |
| RTK_DISABLED prefix | Per-command opt-out | Safety valve | AI overuse | **Keep, rename to DISTILL_DISABLED** |

**Techniques Zap uses that Distill should improve:**

1. **Aggressive code filter → tree-sitter**: Zap's aggressive filter uses regex for import/signature extraction. This is fragile for complex code. Distill should use tree-sitter in v2 for more reliable structural extraction. The regex approach is acceptable for v1.

2. **Passthrough tracking → explicit boolean**: Change from zero-token sentinel to `is_passthrough BOOLEAN`.

3. **`keep_lines_matching` → explicit warning**: Document prominently as "advanced/dangerous". Require minimum 3 tests per filter that uses it.

---

## 4. Missing Opportunities (Not in Zap, Proven in Practice)

These are techniques with production evidence in adjacent systems that Zap does not implement. Each is worth considering for Distill.

---

**M1. Exact Duplicate Line Removal**
- *Where used:* Every log aggregation tool. Python's `Counter`, Unix `sort | uniq -c`
- *Proven in:* Production log processing since the 1970s
- *Trade-off:* Must decide whether to show count ("×47") or simply deduplicate silently. Show count — transparency.
- *Maturity:* Proven
- *Recommendation:* Include in Distill v1 as an optional stage. `deduplicate_consecutive: true` removes adjacent duplicate lines (like `uniq`). `deduplicate_global: true` removes all duplicates anywhere in output (with count shown).

**M2. Repetition Counting (Group-by Pattern)**
- *Where used:* pytest `-q` mode, cargo test output, Sentry event grouping
- *Example:* 200 lines each saying "warning: unused variable `x`" → "warning: unused variable `x` (×200 instances)"
- *Proven in:* Test runners, error tracking systems — production
- *Trade-off:* Requires defining what constitutes "the same" message (exact match, or regex-normalized)
- *Maturity:* Proven
- *Recommendation:* Add `deduplicate_by_pattern` stage: a list of regex patterns; lines matching the same normalized form are grouped with count. This is the most valuable missing feature in Zap.

**M3. Stack Trace Frame Deduplication**
- *Where used:* Sentry, Bugsnag, pytest's `--tb=short`, Python's `traceback` module `limit` parameter
- *Proven in:* Production error tracking systems
- *Implementation:* Detect stack trace blocks (starts with "Traceback (most recent call last):" or equivalent), keep only user-code frames (filter out site-packages, stdlib frames), keep the final exception line
- *Trade-off:* Must identify "user code" vs. "framework code" — typically by path prefix
- *Maturity:* Proven (pytest does this natively with `--tb=short`)
- *Recommendation:* Distill v1, as a pytest-specific filter stage. The concept generalizes to any stack trace format.

**M4. JSON / YAML Structural Compression**
- *Where used:* API gateway middleware, data pipelines, kubectl/terraform output processing
- *Proven in:* Production API processing
- *Implementation:* Parse JSON/YAML, remove null/empty fields, optionally remove specified key paths, serialize back
- *Critical safety rule:* Only apply when the output is PURE JSON/YAML. Mixed output (JSON interleaved with text) is not safe.
- *Trade-off:* Parse-serialize roundtrip must be lossless for non-removed content. Some JSON values are order-dependent (arrays).
- *Maturity:* Proven in data processing; less proven specifically for prompt compression
- *Recommendation:* Distill v2. High value for `kubectl`, `terraform show`, `aws` CLI output. Start with null-field removal only.

**M5. Basic Secret Detection**
- *Where used:* GitHub Secret Scanning, detect-secrets (Yelp), Trufflehog (Trufflesecurity), pre-commit hooks
- *Proven in:* Production at scale
- *Implementation:* Pattern matching against known token formats (GitHub: `ghp_[A-Za-z0-9]{36}`, AWS: `AKIA[0-9A-Z]{16}`, Anthropic: `sk-ant-[...]`)
- *Trade-off:* False positive rate matters. A false positive that redacts a legitimate value could confuse the AI. Flag, don't silently redact.
- *Maturity:* Proven
- *Recommendation:* Distill v1. Warn on stderr: "[distill] Potential secret detected in output — review with `distill audit git-log`". Never block or redact silently.

**M6. Hash / ID Abbreviation**
- *Where used:* git's `--abbrev-commit` (production), container IDs in docker output
- *Proven in:* git itself — 8 hex chars is the standard abbreviated commit hash
- *Implementation:* Regex replace full SHA-256/SHA-1 hashes with first 8 chars (with `...` suffix)
- *Trade-off:* If the AI needs to run a subsequent command using the full hash, the abbreviated version may not work. Solution: include full hash for the most recent/important item, abbreviate historical ones.
- *Maturity:* Proven
- *Recommendation:* Distill v1, as an optional `abbreviate_hashes: true` stage. Default off.

**M7. Log Level Filtering**
- *Where used:* ELK Stack, Splunk, Loki, Fluentd — all production log aggregation tools
- *Proven in:* Production at massive scale
- *Implementation:* Detect log format (detect JSON logs, logfmt, traditional syslog), filter by level (FATAL > ERROR > WARN > INFO > DEBUG > TRACE). Keep only ERROR and above by default.
- *Trade-off:* INFO context may be needed to understand errors. Configurable minimum level.
- *Maturity:* Proven
- *Recommendation:* Distill v1, as a built-in stage `min_log_level: ERROR`. Applies only when output is recognized as log format.

**M8. Diff Context Reduction**
- *Where used:* git itself (`--unified=0` to `--unified=N`), patch utilities
- *Proven in:* Production (git is the canonical implementation)
- *Implementation:* Parse unified diff format, reduce context lines from 3 to 1 (or 0)
- *Trade-off:* AI benefits from some context around changes; 1 line is usually enough
- *Maturity:* Proven (git format is a standard)
- *Recommendation:* Distill v1, as a `diff_context_lines: 1` option in the git filter. The unified diff format is well-specified.

**M9. Timestamp Stripping / Normalization**
- *Where used:* Docker logs (`docker logs --no-timestamps`), systemd journal, all log formatters
- *Proven in:* Production
- *Implementation:* Regex match against common timestamp formats (ISO 8601, epoch, syslog), replace with empty or "T+"relative
- *Trade-off:* Timestamps matter for "what happened when" — strip only when they're truly noise (e.g., every single line of a build log has a millisecond timestamp)
- *Maturity:* Proven
- *Recommendation:* Distill v1, as an optional `strip_timestamps: true` stage. Default off. Opt-in per filter.

---

## 5. Proven Algorithms Catalogue

Organized by family. Labelled by evidence quality.

---

### Lexical Algorithms

| Algorithm | Description | Evidence | Suitable for Distill |
|---|---|---|---|
| ANSI escape stripping | Regex pattern match on escape sequences | Proven | Yes — core stage |
| Whitespace normalization | Collapse multiple blank lines to one | Proven | Yes — core stage |
| Run-length encoding | Compress repeated characters | Proven | Limited use in text prompts |
| Stop word removal | Remove high-frequency low-content words | Proven (NLP) | No — too lossy for technical output |
| Porter/Snowball stemming | Normalize word forms | Proven (NLP) | No — technical terms are case-sensitive |
| BPE tokenization | Byte-pair encoding for tokenization | Proven | Informative only (for counting, not compression) |

### Structural Algorithms

| Algorithm | Description | Evidence | Suitable for Distill |
|---|---|---|---|
| TF-IDF sentence selection | Select sentences by term frequency-inverse document frequency | Proven (IR) | V2 experimental — requires corpus |
| TextRank / LexRank | Graph-based extractive summarization | Proven (NLP research) | V2 research — requires NLP dependency |
| BM25 ranking | Probabilistic relevance ranking | Proven (IR) | V2 for relevance-based filtering with query context |
| Head/tail selection | Keep first N, last M lines | Proven | Yes — core stage |
| Sentence window chunking | Keep K sentences around an anchor sentence | Proven (RAG) | V2 candidate |

### Syntax-Aware Algorithms

| Algorithm | Description | Evidence | Suitable for Distill |
|---|---|---|---|
| Regex-based comment stripping | Language-specific comment removal | Industry Practice | Yes — v1 |
| tree-sitter AST parsing | Parse-tree based structural extraction | Proven (VS Code, GitHub) | Yes — v2 |
| AST dead code elimination | Compiler-based unused code removal | Proven (compilers) | No — modifies code semantics |
| unified diff parsing | Parse and reformat git diff output | Proven (git standard) | Yes — v1 diff filter |
| JSON/YAML structural filtering | Parse and re-serialize removing specified paths | Proven (data engineering) | Yes — v2 |
| Log level detection | Classify log lines by severity | Proven (log systems) | Yes — v1 optional stage |

### Semantic Algorithms

| Algorithm | Description | Evidence | Suitable for Distill |
|---|---|---|---|
| LLMLingua (token pruning) | Small-LM perplexity-guided token removal | Academic (Pan et al., 2023, Microsoft Research) | Research track only — latency unacceptable |
| LLMLingua-2 | Improved version with classification head | Academic (2024) | Research track only |
| Selective Context | Mutual information-based context selection | Academic (Li et al., 2023) | Research track only |
| RECOMP | Retrieval-augmented compression | Academic (Xu et al., 2023) | Research track only |
| Sentence BERT embedding | Semantic similarity for deduplication | Proven (production RAG) | V2 optional with embedding dependency |
| Cosine similarity deduplication | Remove near-duplicate blocks | Industry Practice | V2 optional |

### Domain-Specific Algorithms (Proven in Production)

| Algorithm | Description | Evidence | Suitable for Distill |
|---|---|---|---|
| Test failure extraction | Keep only failed tests from runner output | Proven (pytest, cargo test) | Yes — v1 core |
| Build error extraction | Keep only error lines from build output | Proven (compilers, CI) | Yes — v1 core |
| Stack trace deduplication | Keep user frames, remove framework frames | Proven (Sentry, Bugsnag) | Yes — v1 |
| Git output compression | Abbrev hashes, oneline format, subject only | Proven (git itself) | Yes — v1 core |
| Package install filtering | Keep installed/failed, strip download noise | Proven (pip, npm, cargo) | Yes — v1 |
| Container status formatting | Strip verbose container metadata | Industry Practice | Yes — v1 |
| Cloud CLI output compression | Remove unchanged resource state | Industry Practice | Yes — v1 (terraform, kubectl) |

### Safety Algorithms

| Algorithm | Description | Evidence | Suitable for Distill |
|---|---|---|---|
| Regex secret detection | Match known token formats (AWS, GitHub, etc.) | Proven (detect-secrets, Trufflehog) | Yes — v1, flag only |
| Entropy-based detection | Flag high-entropy strings as potential secrets | Proven (Trufflehog) | Yes — v1, flag only |
| PII NER detection | Identify names/emails/phones using NER | Industry Practice (Presidio) | V2 optional, default off |

---

## 6. Industry Survey

Tools and research examined.

---

### Zap (already fully analyzed)
*Strengths:* Fast Rust binary, comprehensive rule coverage, elegant TOML filter system, inline tests, multi-agent support
*Weaknesses:* Unix/macOS only, no streaming, no duplicate removal, no stack trace deduplication, no secret detection, naming inconsistency
*Unique ideas:* RegexSet dispatch, last-match specificity, transparent prefix recursion, passthrough zero-token recording

---

### LLMLingua (Microsoft Research, 2023–2024)
*Type:* Academic research, open source (MIT license, available on GitHub/HuggingFace)
*Approach:* Use a small "compressor" LM to estimate token importance via conditional log probability; prune tokens below threshold
*Strengths:* Proven on multiple benchmarks; 2–20x compression ratios; lossless on key information
*Weaknesses:* Requires running a second LM (adds 200ms–2s latency); requires `transformers` dependency (~2GB); not suitable for streaming; quality degrades on highly technical/code content vs. natural language
*Unique ideas:* Perplexity as a proxy for token importance; iterative compression with a budget constraint
*Adoption:* LlamaIndex experimental integration; not widely deployed in production as of mid-2026
*Recommendation for Distill:* Research track only. Incompatible with Distill's latency requirements for interactive hook use. However, the conceptual framing — "which tokens would the model attend to?" — is valuable as a design principle.

---

### LlamaIndex Context Compression
*Type:* Open source framework (MIT), production-level
*Approach:* Multiple strategies: `LLMChainFilter` (use LLM to decide relevance), `EmbeddingsFilter` (cosine similarity threshold), `SentenceTransformersRerank` (reranking by relevance), `SentenceWindowNodeParser` (local context windows)
*Strengths:* Production-ready, multiple strategies, composable
*Weaknesses:* Primarily designed for RAG (retrieval context), not command output; LLM-based strategies have latency; requires Python ML stack
*Unique ideas:* The `EmbeddingsFilter` approach — embed chunks, compute cosine similarity to a query, keep only above threshold — is suitable for Distill's v2 if an embedding model is available
*Recommendation for Distill:* The `EmbeddingsFilter` concept (without the full LlamaIndex dependency) is worth adopting for context-aware filtering in v2.

---

### LangChain Document Compressors
*Type:* Open source framework (MIT), production-level
*Approach:* `ContextualCompressionRetriever` with pluggable compressors: `LLMChainExtractor` (LLM-based), `EmbeddingsRedundantFilter` (semantic deduplication), `CohereRerank` (commercial reranking)
*Strengths:* Production-ready, pluggable architecture is well-designed
*Weaknesses:* Designed for RAG retrieval, not terminal output; LLM compressors have unacceptable latency for interactive use; complex dependency chain
*Unique ideas:* The pluggable compressor protocol is architecturally clean; similar to what Distill's `FilterPlugin` should be
*Recommendation for Distill:* The architectural pattern (pluggable compressor chain) is directly applicable. The LLM-based strategies are not.

---

### Aider (AI coding assistant)
*Type:* Open source (Apache 2.0), production-level
*Context management approach:* Uses tree-sitter to build a "repository map" — a compressed representation of all function signatures and class definitions in the codebase. Sends the map, not full file contents, to the AI unless files are explicitly added.
*Strengths:* Proven in production; tree-sitter integration enables accurate structural extraction; repository map concept is genuinely clever
*Weaknesses:* Specific to its own architecture; not designed as middleware
*Unique ideas:* The **repository map** concept: instead of filtering output, proactively describe the codebase structure at startup. This is complementary to, not competitive with, Distill's command output filtering.
*Recommendation for Distill:* The tree-sitter integration for code structure extraction (our "aggressive code filter") should follow Aider's approach in v2. The repository map concept is out of scope for Distill v1 but is a natural v3 feature.

---

### Prompt optimization research (DSPy, OPRO, AutoPrompt)
*Note:* These tools optimize the prompts that humans SEND to AI, not the output that AI receives. They are a different problem domain from Distill.
- **DSPy (Stanford):** Optimizes prompt structure through compilation. Not applicable to command output filtering.
- **OPRO (Google, 2023):** Uses the LLM to optimize its own prompts. Not applicable.
- **AutoPrompt (2020):** Gradient-based prompt search. Not applicable.
*Recommendation:* These are not relevant to Distill's problem space. Note this explicitly so contributors don't propose integrating them.

---

### detect-secrets (Yelp)
*Type:* Open source (Apache 2.0), production-level, widely adopted
*Approach:* Plugin-based secret detection with known pattern plugins (AWS, Slack, GitHub, private keys, etc.) plus entropy-based detection; designed to run in pre-commit hooks
*Strengths:* Mature, well-maintained, configurable allowlisting, good false-positive handling
*Weaknesses:* Designed for code files, not command output; some patterns are file-path aware
*Unique ideas:* The allowlisting mechanism (baseline file of known false positives) is directly applicable to Distill's secret detection
*Recommendation:* Do not take `detect-secrets` as a dependency (too heavy). Borrow its pattern list and the allowlisting concept for Distill's secret detection stage.

---

### Claude Code's Native Context Management (Claude Code Expert perspective)
*What Claude Code does natively:*
- Automatic context summarization when approaching context limit (compresses earlier conversation)
- File caching via `<file_content>` tags — files are deduplicated within a session
- System prompt for CLAUDE.md files — injected once, not repeated
- Tool result size limits (configurable)

*What Claude Code does NOT do:*
- Command output compression before insertion into context
- Tool-call-level filtering
- Per-command compression rules

*Implication for Distill:* Distill's hook operates BEFORE Claude Code inserts command output into context. This is the right interception point — we compress before the AI sees it, so Claude Code's own summarization is less likely to destroy important content that was already sent. Distill and Claude Code's native compression are complementary, not competitive.

*Risk:* Claude Code may add native command output filtering in a future version, reducing Distill's value. Monitor the Claude Code changelog.

---

## 7. Scientific Evidence

Distinguishing facts from opinion, carefully.

---

### What Has Been Rigorously Validated (Peer-Reviewed Academic Evidence)

**Context length affects model performance.**
Multiple studies have shown that LLMs can miss information in the middle of long contexts (the "lost in the middle" problem, Liu et al., 2023). This supports the intuition that shorter, denser contexts improve AI quality — but does not prove that Distill's specific filtering improves task success.

**Prompt compression can preserve task performance.**
LLMLingua (Pan et al., 2023, Microsoft Research) demonstrated that their perplexity-guided compression achieves 2–20x token reduction with <10% degradation on QA benchmarks. However: these benchmarks use natural language, not technical command output. Generalization to Distill's domain is unproven.

**Selective Context (Li et al., 2023)**
Demonstrated that approximately 50% of tokens in prompts are "dispensable" — removing them does not significantly degrade GPT-3.5/4 performance on in-context learning tasks. Caveat: same natural language benchmark limitation.

**Extractive summarization is effective for information retrieval.**
Decades of IR research (TF-IDF, BM25, TextRank) demonstrate that statistical methods can select the most informative sentences from documents. However, these were validated on natural language documents, not terminal command output.

**tree-sitter is accurate for code parsing.**
Production validation in VS Code, Neovim, GitHub Linguist. Accurate parse trees for all major programming languages. This validates the approach for Distill's aggressive code filter in v2.

---

### Industry Best Practice (Widely Adopted, Not Peer-Reviewed)

- **ANSI stripping**: Universal in terminal output processing. Zero controversy.
- **Test failure isolation**: pytest's `--tb=short`, cargo test's `--test`, all CI systems strip passing tests. Widely validated in practice.
- **Log level filtering**: Every log aggregation system does this. ELK, Splunk, Loki, CloudWatch — all production-validated.
- **Stack trace frame deduplication**: Sentry's event grouping, pytest's `--tb=short`. Production-validated.
- **Git output compression**: `--oneline`, `--abbrev-commit`, `--stat` are built into git and widely used. Production-validated.
- **Secret detection patterns**: GitHub Secret Scanning (hundreds of millions of repositories), detect-secrets (Yelp, widely adopted). The pattern sets are production-validated.

---

### Widely Claimed But Unproven

**"Compressed output improves AI coding task quality."**
This is the central claim of Zap and Distill. It is intuitive and plausible, but as of mid-2026, there is no published, controlled study measuring AI task success rate (not just token savings) with and without output compression. Every savings number in Zap's README is estimated token reduction, not a measured improvement in AI task completion.

*Distill must not repeat this error.* The README should state: "Distill reduces token consumption. We believe this improves AI session quality by preserving context space for more important information. If you observe this (or the opposite), tell us — we want real data."

**"4 characters = 1 token."**
A rough approximation that Zap uses. The actual ratio varies: English prose is closer to 4 chars/token; code with long identifiers and operators varies from 2–6 chars/token; Unicode text can be 1 char/token. For aggregate savings estimates over thousands of commands, the approximation is acceptable. For individual command savings, it can be off by 50%. Always display with a confidence indicator.

**"Higher token savings = better."**
False. A filter that saves 90% of tokens by stripping all warning messages has saved tokens at the cost of removing potentially critical information. The goal is maximum savings with zero meaning loss, which is fundamentally a quality question, not a quantity question.

---

## 8. Opportunity Matrix

Complete prioritization across all identified techniques.

| Technique | Expected Savings | Risk | Difficulty | Maturity | Recommended Version |
|---|---|---|---|---|---|
| ANSI stripping | 5–15% | None | Trivial | Proven | **V1 Core** |
| Whitespace normalization | 2–8% | None | Trivial | Proven | **V1 Core** |
| Exact duplicate line removal | 5–40% | None | Low | Proven | **V1** |
| strip_lines_matching | 20–80% | Low–Medium | Low | Proven | **V1 Core** |
| max_lines cap | Safety net | Low | Trivial | Proven | **V1 Core** |
| on_empty fallback | Behavioral | None | Trivial | Proven | **V1 Core** |
| head_lines / tail_lines | 20–80% | Medium | Trivial | Proven | **V1 Core** |
| Test failure extraction | 70–95% | Low | Low | Proven | **V1 Core** |
| Git output compression | 50–80% | Low | Low | Proven | **V1 Core** |
| Build error extraction | 60–90% | Low | Low | Proven | **V1 Core** |
| Secret detection (flag only) | N/A | Safety | Low | Proven | **V1 Core** |
| Stack trace deduplication | 50–80% | Low | Low | Proven (adjacent) | **V1** |
| Repetition counting (N×) | 50–99% | Low | Low | Proven (adjacent) | **V1** |
| Diff context reduction | 20–50% | Low | Low | Proven | **V1** |
| Hash abbreviation | 1–3% | Low | Trivial | Proven | **V1** |
| Timestamp stripping | 2–5% | Low | Low | Proven | **V1 optional** |
| Log level filtering | 60–90% | Low | Low | Proven | **V1** |
| match_output + unless | 0 or 90%+ | Medium | Medium | Proven (Zap) | **V1** |
| replace (regex subst) | 5–20% | Low | Medium | Proven | **V1** |
| keep_lines_matching | 40–90% | High | Low | Proven (Zap) | **V1 (documented danger)** |
| Minimal code filter | 15–35% | Low | Medium | Proven | **V1** |
| Package install filtering | 80–95% | Low | Low | Proven | **V1** |
| truncate_lines_at | 2–10% | Low | Trivial | Proven | **V1** |
| JSON/YAML compression (null removal) | 20–60% | Medium | Medium | Proven (adjacent) | **V2** |
| Aggressive code filter (regex) | 50–80% | High | Medium | Industry Practice | **V2** |
| Aggressive code filter (tree-sitter) | 50–80% | Medium | High | Proven (adjacent) | **V2** |
| Semantic deduplication (embeddings) | 10–30% | Medium | High | Proven (RAG) | **V2 optional** |
| Entropy-based secret detection | N/A | Medium FP | Low | Industry Practice | **V2** |
| PII detection | N/A | High FP | Medium | Industry Practice | **V2 optional, default off** |
| Context-aware filtering | 30–60% | High | Very High | Promising | **Experimental** |
| Reference compression (session) | 10–30% | Low | High | Promising | **Experimental** |
| Context budget awareness | Situational | High | Very High | Promising | **Experimental** |
| LLMLingua token pruning | 50–95% | Medium | Very High | Academic | **Research only** |
| Perplexity-guided compression | 50–95% | High | Very High | Academic | **Research only** |
| Relevance-based (LLM judge) | 40–80% | High | Very High | Academic | **Research only** |
| Attention-guided compression | Unknown | High | Impossible* | Academic | **Never** |

*Attention weights are not accessible from outside the model.

---

## 9. Distill Feature Selection

### Core MVP (Week 1–2)

These are the minimum features to prove the value proposition.

- Claude Code hook adapter (JSON parse, rewrite, respond)
- Compound command splitting (`&&`, `||`, `;`, `|`, `&`)
- Heredoc detection (no rewrite)
- Environment prefix handling
- 5 command filters: `git status`, `git log`, `git diff`, `pytest`, `cat/read`
- `strip_ansi`, `strip_lines_matching`, `max_lines`, `on_empty` pipeline stages
- Basic SQLite tracking (tokens before/after)
- `distill gain` (basic)
- `distill init --claude`

### Strong V1 (Month 1–3)

These features make Distill a complete, trustworthy tool.

- Full 8-stage TOML filter pipeline
- 20+ built-in filters (git, pytest, mypy, tsc, cargo, make, terraform, docker, kubectl, pip, ruff)
- `distill preview <command>` — before/after comparison
- `distill explain <command>` — stage-by-stage trace
- `distill verify [filter]` — inline test runner
- `distill doctor` — health check
- Exact duplicate line removal (new stage: `deduplicate_consecutive`)
- Repetition counting (new stage: `deduplicate_by_pattern` with N× display)
- Stack trace frame deduplication (pytest filter)
- Diff context reduction (git diff filter)
- Log level filtering (optional stage)
- Basic secret detection (flag-only, stderr warning)
- Hash abbreviation (optional stage)
- `distill config show/edit/validate`
- `distill trust` with diff display
- Plugin system (entry-point based, TOML and Python plugins)
- Windows PowerShell hook script
- Cross-platform paths via `platformdirs`

### Future V2 (Month 3–6)

- tree-sitter based aggressive code filter (replacing regex approach)
- JSON/YAML structural compression (null removal)
- Timestamp stripping stage
- `distill health` — filter effectiveness report
- `distill audit <command>` — historical filter trace
- `distill simulate <input-file>` — test filter without running command
- `distill benchmark run` — performance benchmark suite
- `distill off` / `distill on` per project
- Transparent prefix recursion with user-configured prefixes
- Multi-agent support: Cursor (with BOM handling), Gemini
- Session-level tracking (group commands by AI session)
- `distill changelog` — what changed between versions

### Experimental Track (V2+, clearly labelled)

- Embedding-based semantic deduplication (optional `sentence-transformers` dependency)
- Context-aware filtering (reads conversation context from hook JSON)
- Reference compression across a session
- `distill report --format html` with visual savings dashboard

### Research Track (No timeline)

- LLMLingua-style perplexity-guided compression (requires second LM)
- A/B testing infrastructure (measure AI task success with/without filtering)
- Adaptive filters (automatic aggressiveness tuning based on re-run rate)

### Rejected (Explain Why)

- **Abstractive summarization (LLM-based) in core**: Latency unacceptable (200ms–2s). Violates transparency principle (output is a paraphrase, not the actual data). Requires external API call. May introduce hallucinations.
- **Attention-based compression**: Attention weights are not accessible from outside model inference. Cannot implement.
- **Gradient-based prompt optimization (AutoPrompt, DSPy)**: Different problem domain (input prompt optimization, not output compression).
- **AST-based dead code elimination**: Modifies code semantics. Out of scope — Distill does not modify code, only observation output.
- **PII detection as default**: Too many false positives in technical output. IP addresses, email addresses appear legitimately in many contexts. Default on = user frustration.
- **Full LLMLingua integration**: Requires 2GB+ dependency, runs a second LM per command, adds >200ms latency. Incompatible with transparent hook design.

---

## 10. User Trust (Beyond Preview, Doctor, Verify, Explain)

Additional features that build the foundation of trust.

---

**T1. `distill show <command>` — Filter Declaration Before Running**

Before the command executes, show the user WHICH filter will be applied and WHAT it will do:
```
$ distill show "terraform plan"
→ Filter: terraform-plan (built-in, v0.4.2)
→ Will strip: ANSI codes, "Refreshing state" lines, blank lines, "Acquiring state lock" lines
→ Will cap at: 80 lines
→ If empty: "terraform plan: no changes detected"
→ Run anyway: distill terraform plan
```
This is distinct from `preview` (which runs the command). `show` is zero-cost — it describes the plan without execution.

**T2. Conservative Mode / Paranoid Mode**

`DISTILL_MODE=conservative` applies only zero-risk optimizations: ANSI stripping, whitespace normalization, exact duplicate removal. Nothing that could affect meaning.

`DISTILL_MODE=paranoid` disables all filtering. Identical to Zap's `RTK_DISABLED=1` but as a global mode.

Users who are uncertain about Distill's safety can start in conservative mode and graduate to full mode after reviewing a few sessions.

**T3. Side-by-Side Session Log**

`~/.local/share/distill/session.log` records:
- What the AI requested (the raw command)
- What Distill applied (which filter, which stages fired)
- What the AI received (the filtered output)

The log is browsable: `distill log --session last`. This provides a complete audit trail for users who want to verify that Distill never misled the AI.

**T4. Filter Provenance**

Every filter result is annotated (in the session log, not in the output itself) with its source:
- `built-in:pytest:v0.3.1` — a specific version of the built-in filter
- `user-global:my-filters.toml:2026-05-01` — user's personal filter
- `project-local:.distill/filters.toml:2026-06-10 (trusted)` — project filter with trust date

This makes it possible to answer: "Did this filter come from somewhere I trust?"

**T5. Filter Stability Indicator**

In `distill gain` and filter listings, show how long each filter has been stable:
```
pytest filter: 847 invocations · stable 6 months · last tested 2026-06-28 ✓
make filter: 23 invocations · new (2 weeks) · last tested 2026-06-29 ✓
terraform-apply filter: 4 invocations · experimental ⚠
```

Newer filters are marked as "experimental" for the first 30 days and 100 invocations. This sets user expectations appropriately.

**T6. `distill blame <command>`**

If the AI's behavior seems unexpected after a command was filtered, `distill blame "git diff HEAD"` shows:
- The exact filter that was applied
- The exact stages that fired
- The exact content that was removed (the diff)
- A link to the filter's test suite

This is the debugging tool for when Distill might have caused a problem.

**T7. Zero-Claim Mode in Documentation**

The README, documentation, and all marketing materials must distinguish:
- What Distill guarantees: token reduction of at least X% for commands with filters
- What Distill believes but cannot prove: that token reduction correlates with AI session quality
- What Distill measures: tokens before/after, filter hit rate, stage utilization
- What Distill cannot measure: AI task success rate, user productivity improvement

This honesty is itself a trust-building feature. Tools that overclaim lose trust permanently when users notice.

**T8. `distill compatibility check`**

Checks whether the installed Distill version is compatible with the current version of Claude Code and any other registered agents. As agents update their hook API, this check can detect mismatches before they cause silent failures.

---

## 11. Benchmark Strategy

A complete benchmark design for comparing Distill versions, algorithms, and filter profiles.

---

### Benchmark Dimensions

A benchmark must separately measure:
1. **Token reduction** (quantity) — how many tokens saved
2. **Meaning preservation** (quality) — does the filtered output contain everything needed?
3. **Latency** (performance) — how long does filtering take?
4. **Coverage** (completeness) — what % of commands are filtered?
5. **Correctness** (safety) — does any filter remove something it shouldn't?

The most important dimension is **meaning preservation** — and it is the hardest to measure automatically.

---

### Automatic Metrics

| Metric | How to Measure | Limitation |
|---|---|---|
| Token reduction % | `(tokens_before - tokens_after) / tokens_before` | Doesn't indicate quality |
| Filter latency p50/p99 | Timer around filter engine | Only measures Distill, not subprocess |
| Filter hit rate | `filtered_commands / total_commands` | Coverage, not quality |
| Stage utilization | Which stages actually removed content | Identifies dead stages |
| `on_empty` trigger rate | How often output becomes empty | Proxy for over-aggression |
| `must_preserve` patterns | Regex patterns that must appear in filtered output | Partial quality check |

---

### The Quality Problem (And How to Partially Solve It)

Pure automatic metrics cannot measure meaning preservation. The solution: **LLM-as-judge** for benchmark evaluation, used only for benchmarking (not in the live system).

**Benchmark evaluation protocol:**
1. Define a task: "The AI is trying to fix the failing test shown in this pytest output."
2. Give a judge LLM: the full (unfiltered) output and ask "What information is needed to fix the failing test?"
3. Give the same judge LLM: the filtered output and ask "Is all necessary information present?"
4. Score: 0 (critical information missing) / 0.5 (partial) / 1 (all information preserved)

This is slow and costs money — use only for benchmarking, not runtime.

---

### Benchmark Fixture Library

Each fixture is: `{command, sample_output_file, applicable_filters, must_preserve_patterns, must_not_preserve_patterns}`.

**`must_not_preserve_patterns`** (new concept): patterns that should be REMOVED by a correctly functioning filter. If these patterns appear in filtered output, the filter is too conservative.

Example:
```yaml
name: pytest-5-failures-200-passing
command: pytest tests/
applicable_filters: [pytest]
must_preserve:
  - "FAILED tests/"           # failure lines must appear
  - "AssertionError"          # error messages must appear
  - "short test summary"      # summary section must appear
must_not_preserve:
  - "PASSED"                  # passing tests must be removed
  - "^\\."                    # progress dots must be removed
token_reduction_target: 0.75  # must achieve at least 75% reduction
```

---

### Benchmark Suite Categories

**Category 1: Baseline (deterministic, always passing)**
Fixtures with known input and expected output. Must pass on every commit. These are the inline tests, extended with additional coverage.

**Category 2: Regression (version comparison)**
Run before and after a version change. Report: did any filter's token reduction drop? Did any filter's `must_preserve` pass rate drop? Visualize as a table.

**Category 3: Performance (latency)**
Run each filter against progressively larger inputs (1KB, 10KB, 100KB, 1MB). Report: latency p50, p99, p999. Flag if p99 > 200ms.

**Category 4: Quality (LLM-as-judge)**
Run monthly using the LLM evaluation protocol described above. Score: meaning preservation rate per filter. Target: ≥95% across all filters. This is the most important benchmark and cannot be automated cheaply.

**Category 5: Coverage (real session replay)**
Record a real AI coding session (anonymized). Run it through Distill. Report: hit rate, savings by category, uncovered commands. Drive roadmap prioritization.

---

### Benchmark Tooling

```
distill benchmark run --category baseline        # Fast, runs in CI (<30s)
distill benchmark run --category regression      # Compare versions (requires baseline)
distill benchmark run --category performance     # Latency profiling
distill benchmark run --category quality         # LLM-as-judge (costs $, run manually)
distill benchmark compare v0.1.0 v0.2.0         # Side-by-side comparison
distill benchmark report --output html           # Visual report
```

Results are stored in `~/.local/share/distill/benchmarks/` with timestamps.

---

## 12. Risks

Ranked by: Severity × Probability. All three dimensions covered.

---

### Technical Risks

| Risk | Severity | Probability | Mitigation |
|---|---|---|---|
| Hook produces wrong output → AI session broken | Critical | Low (if tested) | Error contract tests; always return valid JSON |
| Python startup latency >100ms on Windows (AV software) | High | Medium | Measure on target environment; document; consider compiled helper |
| Regex catastrophic backtracking hangs hook | High | Medium | Use `regex` package; add per-stage timeout |
| Filter strips needed information (false negative) | High | Medium | `distill preview`, inline tests, conservative defaults |
| Windows path GLOB produces wrong project scoping | High | High | Fix before v1; `Path.as_posix()` normalization; Windows CI |
| SQLite database corrupted under concurrent writes | Medium | Low (WAL mode mitigates) | WAL mode; busy timeout; test concurrent access |
| Plugin API breaks community plugins after upgrade | High | Medium (if not planned) | Finalize API spec before v1.0; semver major for breaks |
| Python venv path in hook script → hook silently fails | High | High | Embed full Python path in hook script; verify in `distill doctor` |
| Claude Code changes hook JSON format | High | Medium | Version-check in `distill doctor --test-hook`; monitor release notes |
| Filter for tool version N fails silently for tool version N+1 | Medium | High (CLI tools update) | `distill health` shows filter effectiveness by invocation |
| Secret pattern matching: false positive redacts important output | Medium | Medium | Flag only, never silent redact; user can suppress |
| Input size unbounded → memory exhaustion | Medium | Low (rare) | 10MB cap; test with large outputs |

---

### UX Risks

| Risk | Severity | Probability | Mitigation |
|---|---|---|---|
| User discovers filter removed critical info → trust destroyed | Critical | Low (if preview exists) | `distill preview` before v1; conservative defaults |
| Hook silent failure → user thinks Distill is working but it isn't | High | Medium | `distill doctor` with synthetic hook test; onboarding mode |
| Corporate Python path issues → installation success, hook fails | High | High on Windows | Embed absolute Python path; verify in `distill doctor` |
| Confusing `distill gain` numbers (estimated vs. real) | Medium | High | Label all estimates explicitly; show confidence range |
| Configuration too complex → users give up | Medium | Medium | Sensible defaults; most users should never need config |
| Multiple Python installations → `distill` runs in wrong context | High | Medium (Windows) | Use `sys.executable` for hook script Python path |
| `distill trust` UX too confusing → users skip security step | Medium | Medium | Show what changed in filter since last trust; clear language |

---

### Performance Risks

| Risk | Severity | Probability | Mitigation |
|---|---|---|---|
| Python startup adds 100–300ms latency on every AI command | Medium | High | Measure; if unacceptable, consider a persistent daemon process |
| Filtering large outputs (>1MB) blocks hook for seconds | High | Low (with cap) | 10MB hard cap; benchmark at 1MB |
| SQLite write latency spikes under concurrent access | Medium | Low (WAL mitigates) | WAL mode; async write via thread |
| regex catastrophic backtracking on malformed output | High | Medium | `regex` package; timeout wrapper |

**The Python startup latency risk deserves elaboration (Performance Engineer perspective):**

A Rust binary starts in <10ms. Python starts in 50–150ms depending on installed packages and AV software. On a corporate Windows machine with endpoint detection software, Python startup can reach 500ms+.

If Claude Code runs 200 commands in a session and each adds 100ms: 20 additional seconds of wall-clock time per session. This is likely imperceptible — each command takes 0.5–30 seconds of actual execution. But if the AI runs `git status` 10 times in rapid succession during a task, the 100ms per-command hook overhead becomes 1 second of added latency.

**Mitigation options to evaluate:**
1. **Accept it**: For interactive coding sessions, 100ms per-command hook overhead is within acceptable latency
2. **Persistent daemon**: Hook script connects to a persistent Distill daemon (socket/pipe). One Python startup per session, not per command. Added complexity: daemon lifecycle management, socket cleanup
3. **Pyinstaller bundle**: Package Distill as a self-contained executable (like Zap). Faster startup but loses the "pure Python" advantage

**Recommendation**: Measure actual startup time on Windows in CI. If p99 > 200ms, design the daemon architecture before v1.0, not after.

---

### Product Risks

| Risk | Severity | Probability | Mitigation |
|---|---|---|---|
| Claude Code adds native command output filtering | High | Medium | Distill's value extends to multi-agent support; differentiate on transparency and extensibility |
| AI models improve to have 1M+ token context → filtering less valuable | Medium | Medium (2+ year horizon) | Filtering remains valuable for cost even with large context |
| Filters fail silently as CLI tools update output formats | High | High | `distill health` shows filter effectiveness; community update process |
| Community filter quality is low → users lose trust | Medium | Medium | CI tests mandatory; review process for built-in filters |
| Name collision with `distill` on PyPI | High | High | Choose package name NOW. Verify availability. |

---

### Open Source Governance Risks

| Risk | Severity | Probability | Mitigation |
|---|---|---|---|
| Single maintainer burnout | High | High (all solo projects) | Design for external contribution; TOML filters are the primary contribution path |
| Filter quality variance with community contributions | Medium | High | Minimum 3 inline tests per filter; CI enforcement |
| Breaking plugin API change alienates contributors | High | Medium | Finalize API spec before v1.0; never break between minor versions |
| Security issue in community filter (malicious pattern) | Medium | Low | Review process; TOML filters are sandboxed to regex; no arbitrary code execution |
| Stale filters for EOL tools accumulate | Low | High | Versioning; deprecation process; `distill health` shows zero-usage filters |
| PR backlog when project gains traction | Medium | Medium | Documented contribution guidelines; automated CI that approves simple filter additions |

---

## 13. Final Architecture Review

Reviewing the proposed architecture from the design-review.md document. Challenging what needs challenging.

---

### What to Change

**Architecture Challenge 1: The TOML filter format should be redesigned.**

Zap's TOML format has three problems we should fix before v1:

1. `strip_lines_matching` and `keep_lines_matching` are mutually exclusive but the schema doesn't enforce it. Two users will have bugs from setting both.
2. The `replace` stage has a list of `{pattern, replacement}` dicts, but there's no way to define "apply this pattern only if the output is above N lines" (conditional stages). This limits expressiveness.
3. There's no way to compose filters (inherit from another filter and override specific stages).

**Proposed Distill TOML redesign:**
```toml
[filter.pytest]
description = "Compact pytest output"
match_command = "^pytest\\b|^python -m pytest\\b"
inherits = "base-test-runner"    # NEW: inheritance

[[filter.pytest.stages]]
type = "strip_ansi"

[[filter.pytest.stages]]
type = "deduplicate_consecutive"   # NEW stage

[[filter.pytest.stages]]
type = "strip_lines"              # Clearer name than strip_lines_matching
patterns = ["^PASSED", "^\\.+"]

[[filter.pytest.stages]]
type = "group_repeated"           # NEW: repetition counting
  patterns = ["^(warning|note):"]

[[filter.pytest.stages]]
type = "max_lines"
limit = 100
```

The `stages` array with typed stages is more explicit than Zap's implicit ordering. It also enables future stages (like `type = "summarize"`) without changing the schema.

**Architecture Challenge 2: The classifier uses regex patterns compiled at import time, but Python's regex compilation is sequential.**

If Distill has 60+ rules, compiling 60 regex patterns at import time takes ~20–50ms. This is fine for normal operation but slow for the hook (which starts per-command).

**Solution**: Lazy compile — compile each regex pattern only when first used. The hook typically matches the same small set of commands in a session. The 60-rule compile cost is only paid if all 60 pattern types are encountered.

Alternative: Pre-compile to `.pyc` with the patterns embedded. Not straightforward with `re.compile()`.

**Architecture Challenge 3: The plugin discovery mechanism (entry-points scanning) is slow.**

`importlib.metadata.entry_points()` scans all installed packages on the first call. This adds 20–100ms on Python startup for environments with many packages (common in corporate Python environments with all-in-one installs).

**Solution**: Cache the discovered plugins in `~/.config/distill/plugin-cache.json`. Invalidate the cache when any package is installed/removed (use `importlib.metadata` to get a hash of the installed package set). On normal runs, load from cache (fast). On package install/remove, rebuild cache.

**Architecture Challenge 4: The 8-stage TOML pipeline assumes linear execution. It should support branching.**

Currently, `match_output` is the only short-circuit path. But there are useful conditional structures:
- "If the output matches pattern X, apply stage set A; otherwise apply stage set B"
- "If `pytest` version 7+ format detected, use these patterns; otherwise use those"

This is over-engineering for v1. But the pipeline should be designed as a DAG (directed acyclic graph), not a fixed linear sequence, so branching can be added in v2 without a schema break. **Recommendation**: Design the internal `FilterEngine` as a stage executor that can handle a list of stages (not a fixed 8-step function). TOML format v1 maps to a linear list. V2 can add conditional stages without breaking v1 parsers.

**Architecture Challenge 5: The tracking database is synchronous, blocking the hook.**

Every filtered command does a synchronous SQLite write before returning. If the database is on a slow disk or experiencing contention, this adds latency to the hook.

**Solution**: Write to the database asynchronously in a background thread. The hook returns immediately after filtering. The database write happens in a daemon thread. Failure in the background write is logged but does not block the command.

```python
import threading
def record_async(db, command, project, tokens_before, tokens_after, exec_ms):
    t = threading.Thread(target=db.record, args=(...), daemon=True)
    t.start()
    # Don't join — let it complete in background
```

**Architecture Challenge 6: The hook entry point design has a critical gap.**

The hook is invoked as: `python -m distill hook claude`

The command reads from stdin, processes, writes to stdout. But what if the command's output (stdout) is line-buffered and Claude Code times out waiting for the response?

Python's stdout is line-buffered in interactive mode and block-buffered in non-interactive mode. When invoked as a subprocess, Python is in non-interactive mode — stdout is block-buffered with a 4KB buffer. If the hook output is less than 4KB (almost always true), it may be buffered and never flushed before the process exits.

**Solution**: `sys.stdout.flush()` explicitly after writing the hook response. Or: use `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, line_buffering=True)`. This must be tested — it's a silent failure mode.

---

### What Is Correct

- `platformdirs` for cross-platform paths: correct
- `importlib.resources` for built-in TOML files: correct
- `@lru_cache` for classifier: correct
- `regex` package instead of `re`: correct (confirmed recommendation)
- Separate `classifier.py` from `rules.py`: correct
- `is_passthrough BOOLEAN` in SQLite schema: correct
- Schema version in filter TOML files: correct
- Plugin API via entry-points: correct (despite the caching issue noted above)
- Conservative defaults, explicit opt-in: correct

---

### The One Missing Abstraction

**The pipeline should have a pre-execution hook and a post-execution hook.**

Before the subprocess runs: `on_before_execute(command: str) -> Optional[str]`
After the subprocess completes: `on_after_execute(command: str, output: str) -> FilterResult`

This enables:
- Caching: "I've seen this exact command recently, return the cached filtered output"
- Context injection: "Before git diff runs, check if we need to set --unified=1"
- Post-execution annotation: "Add a note to the output: '37 test files, 5 failing'"

Not for v1. But the `FilterPlugin` protocol should include optional `before_execute` and `after_execute` methods (default: None) so plugins can opt in to this in v2 without changing the core API.

---

## 14. What Would We Build From Scratch?

Setting aside Distill and Zap. Designing from zero, with full knowledge of the domain.

---

### The Fundamental Reframe

Zap and the proposed Distill architecture share an assumption: **the unit of optimization is a single command's output**.

This is operationally correct (the hook fires per-command) but strategically incomplete. The AI doesn't care about individual commands — it cares about completing a task. The question is not "what is the minimum representation of `pytest tests/ -x` output?" but "what does this AI need right now to continue making progress on its current task?"

A smarter system would be **task-aware**. Not necessarily AI-powered, but session-aware.

---

### The Architecture We Would Build

**Name**: Lens (working name — it focuses context rather than distilling it)

**Core insight**: The right abstraction is not a command filter, but a **context optimizer**. It manages what the AI sees across an entire session, not just per-command.

**Three layers:**

**Layer 1: Command Filter (same as Distill/Zap)**
Per-command output filtering using the same TOML pipeline. This is table stakes. No change from what we've designed.

**Layer 2: Session Memory**
Track what the AI has already seen in this session:
- Which files have been read (and their hash — detect changes)
- Which errors have been seen (deduplicate repeated errors across commands)
- Which test failures have been reported (don't re-report the same failure if it's still failing)
- The current "working set" of files the AI is focused on

This does NOT require reading the AI's conversation. It tracks what Distill has SENT to the AI — that data is entirely within Distill's control.

**Layer 3: Context Budget Manager (experimental, v3)**
Track (approximately) how much context the AI has consumed. As the session progresses:
- Early session: normal filtering (conservative)
- Mid session: standard filtering
- Late session (context filling): aggressive filtering, dedup with session memory
- Near limit: warn the AI via a system message injected into the hook response

This requires knowing the context window size and an estimate of current usage — not yet accessible from hooks, but potentially available in Claude Code's hook context JSON.

---

### Comparison: Distill vs. "Lens"

| Feature | Distill | Lens |
|---|---|---|
| Per-command filtering | Yes | Yes (same) |
| Session-level deduplication | No | Yes |
| Context budget awareness | No | Yes (v3) |
| Complexity | Low | Medium (v1), High (v2+) |
| Installation friction | Low | Low (same) |
| Single maintainer risk | Medium | Higher |
| Community contribution path | Clear (TOML filters) | Less clear (session logic is Python-only) |
| Transparency | High | Lower (session state adds opacity) |
| Time to v1 | 2 weeks | 4+ weeks |

---

### Should Distill Change Direction?

**No — with one addition.**

Distill's command-level approach is:
1. Achievable in a short v1 timeline
2. Transparent (per-command, understandable)
3. Open to community contribution via TOML
4. Directly comparable to Zap (the competitive reference)

The session-level layer should be added as a **v2 feature**, not redesigned into v1. Specifically:

**Add to v1 design**: The `FilterPlugin` protocol should include an optional `session_context: SessionContext` parameter. In v1, it's always `None`. In v2, the session context tracks what the AI has already seen. This one parameter addition makes the v2 upgrade non-breaking.

**The architectural lesson from "Lens"**: Distill should not think of itself as a command filter. It should think of itself as a **context pipeline**. Commands are one input into that pipeline. Session history is another. Future inputs (active file in editor, current error in IDE) are others. The TOML filter system and plugin architecture are the RIGHT foundation for this vision.

---

## 15. Final Readiness Assessment

Five categories of outstanding questions.

---

### Category 1: Unknowns That Could Change Architecture

**U1. What is Claude Code's hook timeout budget?**

If the hook takes >X milliseconds, Claude Code kills it and passes the original command through. We don't know X from the public documentation. If X is 100ms and Python startup is 100ms on the user's Windows machine, every hook invocation is a race condition.

**Resolution before implementation**: Test empirically on the target environment. Install Claude Code on Windows, write a hook that sleeps for progressively longer intervals, find the timeout. This test takes 30 minutes and must be done before v1.

**U2. Does Claude Code's hook receive the full conversation context in the JSON payload?**

If the hook JSON includes the AI's conversation history (all prior messages), Distill can implement session-level deduplication and context-awareness without any architectural additions. If it doesn't, session features require a separate tracking mechanism.

**Resolution before implementation**: Read Claude Code's hook documentation carefully. Test with a hook that logs the full stdin JSON. This determines whether "Lens"-style session awareness is achievable within the hook architecture.

**U3. What is the hook invocation mechanism on Windows (PowerShell vs. WSL vs. cmd.exe)?**

The hook script will be invoked by Claude Code as a subprocess. On Windows, which shell interprets the hook? If it's PowerShell, the hook script must be `.ps1`. If it's cmd.exe, it must be `.bat` or `.cmd`. If it's WSL, it must be a bash script with a Linux Python path.

**Resolution before implementation**: Install Claude Code on Windows, inspect `settings.json`, trace the hook invocation. This is the single most important unknown for Windows support.

---

### Category 2: Decisions That Are Expensive to Change Later

**D1. The SQLite schema.**

Adding columns to SQLite requires migrations. The schema designed in design-review.md should be finalized before the first commit. Specifically: `is_passthrough BOOLEAN`, `schema_version`, `session_id` (for future session tracking), `filter_name` (which filter matched), `stages_applied` (comma-separated list for `distill health`).

Add ALL future-useful columns now, with default values, even if they're not used in v1. The cost is zero; the migration cost later is non-trivial.

**D2. The TOML filter format.**

The analysis in Section 13 recommends redesigning the TOML format from Zap's implicit-ordering approach to an explicit `stages` array. If we ship v1 with Zap's format and v2 changes it, every community filter breaks. Decide the format now, before filters are written.

**D3. The package name.**

`distill` is taken on PyPI. This must be resolved before any release. Options: `distill-ai`, `distill-ctx`, `distill-cc`, `promptdistill`, `contextdistill`. Search PyPI, npm (for future JS adapter), and GitHub. Register the name before writing any code that will be committed.

**D4. The plugin API version.**

Version the plugin API from day one: `FilterPlugin.API_VERSION = "1.0"`. Plugins declare which API version they target. The core validates compatibility. This costs nothing to add now; retrofitting it after community plugins exist is a breaking change.

**D5. The filter TOML schema version.**

Add `schema_version = 1` to every built-in filter. Add validation that rejects filters without a schema version (or defaults to 1 for backward compatibility). Future schema changes are then manageable.

---

### Category 3: Research That Should Be Done Before Implementation

**R1. Measure Python startup time on the user's actual environment.**

Run `time python -c "import distill"` on the user's corporate Windows machine. If it's >200ms with corporate security software, the daemon architecture becomes a v1 requirement, not a v2 option.

**R2. Verify the hook JSON format from an actual Claude Code installation.**

Read a sample hook stdin from a real Claude Code session on both Windows and macOS. The JSON format is documented but may have undocumented fields or version-specific differences. 30 minutes of testing prevents weeks of debugging.

**R3. Verify that `pip install --user` on Windows puts `distill` in `%PATH%`.**

This is frequently not the case on Windows. If `distill` is not in PATH after `pip install --user`, `distill init` fails silently. The installation experience must be tested on a fresh Windows machine before designing the onboarding flow.

---

### Category 4: Technical Decisions to Make Now

**T1. Use `regex` package from day one.**

Replace Python's `re` module with the `regex` package for all user-facing pattern matching. It's a drop-in replacement, is actively maintained, and prevents catastrophic backtracking. Add it as a required dependency in `pyproject.toml`. This decision costs nothing to make now and prevents a class of production bugs.

**T2. Stdout flushing in hook entry point.**

The hook must explicitly flush stdout after writing the response: `sys.stdout.flush()`. This is a correctness requirement, not an optimization. Add it to the hook design spec.

**T3. Async database writes from day one.**

Design the tracking database interface with async writes (background thread) from the start. Even if v1 is synchronous, the interface should be `db.record_async(...)`. This prevents the hook from being blocked by database writes.

**T4. The TOML format decision.**

Choose between:
- **Option A**: Match Zap's format exactly (maximizes filter portability; inherits Zap's design flaws)
- **Option B**: Design a clean `stages`-array format (breaks Zap compatibility; is better long-term)

**Recommendation**: Option B. Distill is not Zap. The TOML filter format is the primary contribution interface — it must be clean. Users who migrate from Zap can convert their filters; a migration tool could be written. The long-term quality of the format matters more than day-one Zap compatibility.

---

### Category 5: Final Verdict

**The project is ready for implementation with four preconditions:**

**Precondition 1: Verify Python startup latency and hook timeout on target Windows environment.**
(1 day of testing)

**Precondition 2: Verify Claude Code hook JSON format and invocation mechanism on Windows.**
(1 day of testing)

**Precondition 3: Choose and register the package name on PyPI.**
(1 hour)

**Precondition 4: Finalize the TOML filter schema (Option A or B) as a written spec.**
(1 day of design)

---

**If these four preconditions are met, the project has no remaining unknowns that would cause architectural regret.**

What we know:
- The optimization techniques to implement (Section 8 matrix)
- The correct pipeline ordering and stage semantics (Section 2, Section 13)
- The correct architecture with improvements (Section 13)
- The user journey and friction points (Section 1)
- The trust features needed (Section 10)
- The benchmark design (Section 11)
- The risks and mitigations (Section 12)
- The features for each version (Section 9)
- The engineering principles for contributors (design-review.md, Section 4)

What we do not know (and cannot know without empirical testing):
- Whether filtering actually improves AI task quality (cannot know until a/b testing infrastructure exists — accept this uncertainty and be honest about it)
- Python startup latency on the specific target environment
- Claude Code's exact hook timeout value
- Claude Code's hook JSON format on Windows

**The second and third unknowns are empirically testable in one day. The first is a product risk we must accept and be transparent about.**

**The project is ready to begin implementation.**

The architecture is sound. The risks are identified and mitigated. The feature set is prioritized. The philosophy is defined. The benchmark strategy is designed. The user journey is mapped.

One final recommendation: begin with Precondition 1 (startup latency testing) and Precondition 4 (TOML schema decision). These are the two choices with the highest downstream impact and the lowest cost to make now versus later.

---

*Panel consensus: All eight panelists agree the project is ready to begin implementation after the four preconditions are met. No panelist identified a remaining unknown that would cause significant architectural regret if discovered during implementation.*
