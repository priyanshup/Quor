# Investigation: Deterministic Structural Diff Compression (QB-099)

> Status: **investigation only — no backlog item promoted, no production code written.**
> Produced per QB-099's own scope ("investigate architecture, build a prototype, benchmark
> feasibility, determine whether production implementation is justified... Do not commit").
> Prototype code lives alongside this document, untracked, in
> `docs/design/QB-099-prototype/` (`structural_diff.py`, `benchmark.py`,
> `benchmark_output.txt`) — runnable directly (`python benchmark.py`), no new
> dependencies, no `quor` package changes.

---

## 1. Framing

QB-041 (git-diff compression) already closed the easy half of this problem: `collapse_unchanged_context`
trims long runs of untouched context lines, and `preserve_patterns` correctly protects every real
`+`/`-`/`@@` line. What QB-041's own entry explicitly leaves unsolved (idea 2, "summarize a diff's
own repeated shape") is the harder half: **today, a pure function reorder, a rename, or a moved
class shows up in `git diff` as a full delete-then-insert of every affected line — every one of
those lines is `+`/`-`-prefixed, so `preserve_patterns` protects all of them, and QB-041's own
context-collapsing stage can do nothing about it (there is no unchanged-context run to collapse; it's
all "changed").** A structural, AST-aware diff is the only way to recognize that case at all, because
recognizing it requires comparing *declarations*, not *lines*.

This is a new capability, not a bigger version of an existing stage — same shape of finding as the
prior repo-summarization investigation (`docs/design/repo-summarization-investigation.md`): it needs to read a
whole file's structure and reason about correspondence between two trees, which `StageHandler`/
`ContentMask`'s "one already-captured blob, downgrade `KEEP`→`COMPRESS`, never synthesize" contract
was never built for (Anti-Goal #18). The right shape is a **preprocessing transform that runs before
the git-diff filter's own `ContentMask` pipeline**, producing a new, shorter textual representation of
the diff — which `ContentMask` then compresses further as normal (`max_tokens`, `preserve_patterns`,
etc., all still apply downstream) — not a `StageHandler` itself. This mirrors the architecture already
adopted for `repo_profile` (a parallel pipeline, ContentMask reused only at the very end) rather than
inventing a new integration pattern.

---

## 2. Algorithm research

Four families were evaluated against QB-099's own hard constraints: **no AI, no heuristics, no
probabilistic matching, no fuzzy similarity — deterministic and reproducible only.**

| Tool / algorithm | Language / runtime | License | What it actually gives you | Fit for Quor |
|---|---|---|---|---|
| **GumTree** (Falleri et al., the de facto industry standard — RefactoringMiner, several IDE "smart diff" features build on it) | Java, needs a JVM | **LGPL-3.0** (copyleft) | Full bottom-up + top-down AST matching, producing an edit script (insert/delete/update/move) | **No.** Two independent disqualifiers: (a) LGPL under Quor's Apache-2.0, pip-installable, no-JVM-dependency posture is real friction, not just paperwork; (b) no mature, full-fidelity Python binding exists — the closest thing (`smacker/gum`) is a Go port. Would mean shelling out to a JVM per diff, which is a heavy, slow, new runtime dependency Quor has never taken for any other stage (tree-sitter is a native-compiled Python extension, not an external process). |
| **Difftastic** (Wilfred Hughes) | Rust binary, CLI | MIT | A *readable*, syntax-aware line/hunk diff via Dijkstra's algorithm over an edit graph, using tree-sitter grammars | **No, wrong tool for this job.** License and language support are both fine (tree-sitter overlaps Quor's own), but Difftastic is not built to answer "did declaration X move/rename across the file" — it aligns and colors *one* tree-to-tree edit path for readability, not cross-declaration correspondence. It would need to run as a subprocess (another new external-binary dependency) and still wouldn't produce the rename/move/reorder semantics QB-099 asks for. |
| **ChangeDistiller** (UZH Software Evolution Lab) | Java | Apache-2.0 (license-compatible) | Bottom-up + top-down matching, similar shape to GumTree (predates it, generally regarded as less accurate) | **No.** License is fine, but it's an unmaintained ~2007-era academic research tool, still Java/JVM — same runtime-dependency problem as GumTree, with no accuracy advantage to offset it. |
| **Zhang-Shasha / APTED** (tree edit distance) | **Python-native** (`pip install apted` / `zss`) | MIT | A numeric tree-edit-distance value and a low-level node-to-node mapping between two *full* trees | **Partial — a primitive, not a solution.** This is the only option that's pure Python and license-clean. But it answers a different, lower-level question ("what's the minimum-cost edit path between these two entire trees") than "which function got renamed" — turning a raw node mapping into "renamed"/"moved"/"reordered" phrasing is exactly the classification layer this investigation still had to build regardless of which library sits underneath. It also doesn't obviously map onto files with the size and shape of source code (hundreds of nodes) faster than a coarser, declaration-level comparison — worth a future look if the declaration-level approach's own limits (below) are hit, but not needed for what QB-099 actually asks for. |

**The load-bearing finding:** none of the four is a drop-in fit. The two "real," proven algorithms
(GumTree, ChangeDistiller) are Java tools that would force a JVM subprocess into Quor's pure-Python,
in-process pipeline — a bigger architectural and licensing cost than the compression gain justifies.
The one pure-Python, license-clean option (APTED/zss) is a generic primitive that still leaves the
entire "recognize a refactor pattern" layer unbuilt. **Quor already owns the one piece that actually
matters for Python — a real parser (stdlib `ast`) and a declaration-extraction traversal
(`ast_summarize/python.py`'s `_visit_module_body`/`_visit_class_body`, and
`quor/pipeline/ast_summarize/registry.py`'s per-language equivalents for the tree-sitter languages) —
so the right move is a narrow, purpose-built matcher on top of that, not adopting an external tool.**

**A second finding, independent of tooling choice:** GumTree's own published algorithm is *not*
purely exact/deterministic in the way QB-099 requires. Its second phase (bottom-up matching for
partial, non-isomorphic subtrees) and the refactoring-detection tools built on top of tree diffs more
generally (RefactoringMiner, JDeodorant's extract-method detector) use a **node-content similarity
score against a threshold** to decide "close enough" matches — precisely the "fuzzy similarity" this
ticket's constraints rule out. This isn't a reason to reject the whole idea; it's a reason to scope
the prototype to GumTree's *first* phase only — exact, isomorphic subtree matching — which is
legitimately, fully deterministic on its own. Section 4 shows exactly what that narrower scope can and
cannot detect.

---

## 3. Prototype architecture

Python only, per QB-099's own scope. `docs/design/QB-099-prototype/structural_diff.py` (~340 lines,
zero new dependencies — stdlib `ast`/`difflib` plus one reused import from
`quor.pipeline.ast_summarize.python`).

```
Decl extraction  -- mirrors python.py's _visit_module_body/_visit_class_body exactly
                     (top-level functions/classes, one level into a class for methods,
                     including if/try/with wrappers) — reimplemented standalone at
                     prototype scope for portability; a production version adds this as
                     a third sibling next to those two functions (same tree, a third
                     output: (Symbol, ast.AST) pairs), not a second copy.

Canonicalization -- a position-stripped structural dump of one declaration's AST subtree
                    (ast.iter_fields recursively, dropping lineno/col_offset/etc., which
                    ast.iter_fields already excludes since they're _attributes not
                    _fields). Optionally blinds one identifier (the declaration's own
                    name, everywhere it's referenced as a Name.id/Attribute.attr/nested
                    FunctionDef.name) to a fixed placeholder — this is what makes rename
                    detection deterministic even for a *recursive* function that calls
                    itself by name (see the large_rename benchmark case).

Matching          -- five deterministic passes, in priority order, each operating only on
                     what the previous pass left unmatched:
                     1. exact full-dump match (content byte-for-byte AST-identical) ->
                        unchanged / reordered (same parent, different sibling rank) /
                        moved (different parent — a method relocated across classes, or
                        promoted to module scope, is the same mechanism as reorder,
                        just a different parent)
                     2. exact blinded-dump match, different declared name -> renamed
                     3. same qualified name, different content -> modified
                     4. exact contiguous-statement-subsequence match + a single
                        call-statement replacing it -> extracted / inlined
                     5. anything left -> added / removed

Rendering         -- one line per non-trivial op ("unchanged" declarations are silent —
                     that's the entire point); "modified" gets a difflib.unified_diff
                     scoped to just that declaration's own source lines, not the whole
                     file. A whole file with zero non-trivial ops (content identical,
                     only import order or whitespace/comments differ outside any
                     declaration) is classified "import-only" or "formatting-only"
                     up front instead of walking declarations at all — reusing
                     collect_import_statements_python() directly rather than
                     re-deriving import parsing a second time.
```

### 3.1 Two real bugs the benchmark itself surfaced (both fixed, both instructive)

- **Silently dropping reorder/move information.** An early version classified any file whose only
  changes were `reordered`/`moved` ops as "formatting-only" — technically true (no AST content
  differs), but it discarded exactly the fact QB-099 asked to preserve (*which* declarations moved
  *where*). Fixed: only truly `unchanged` declarations (same content *and* same position) are treated
  as having nothing to report; `reordered`/`moved` always render, just without an expensive body diff
  since the content itself didn't change. Left as originally written, this would have been a real,
  shipped information-loss bug — worth flagging on its own as a caution for any future
  "declaration-level" compression idea: "AST-identical" and "reportable" are not the same test.
- **Double-reporting a nested change.** The prototype's declaration list is *flat* across two levels
  (top-level, and one level into a class) — the same shape `extract_symbols_python()` already uses.
  When only one method inside a class changed, both the class itself (qualname-matched, "modified",
  full text diff) *and* the method itself (qualname-matched, "modified", its own smaller diff) fired
  independently — the same change reported twice, in the `method_extraction` benchmark case actually
  making the structural rendering *larger* than the plain diff baseline (measured: −11.3% before the
  fix). Fixed by suppressing a class's own text diff whenever member-level ops already exist for it —
  but the fix is a patch over the real gap: **a production implementation needs genuinely
  hierarchical matching (recurse into a container's members only once you know the container itself
  doesn't already match exactly), not two independent flat passes that happen to share a rendering
  post-fix.** This is more implementation work than "reuse `ast_summarize`'s existing flat traversal
  as-is" assumed going in — see Section 6.

---

## 4. Benchmark results

9 synthetic Python cases (7 requested by QB-099, plus one positive control and one dedicated
cross-class method-move case — see §4.1 for why). Baseline = `git diff --no-index -U3` between two
temp files (a fair proxy for "what Quor's git-diff filter feeds into `ContentMask` today," since
`context_lines` defaults to 3 in `collapse_unchanged_context` — see that stage's own config default).
Token counts use Quor's own `ceil(len/4)` estimator (`quor/pipeline/stages/_utils.py`'s
`line_tokens()` convention), for an apples-to-apples comparison. Full output:
`docs/design/QB-099-prototype/benchmark_output.txt`.

| Case | Baseline tokens | Structural tokens | Reduction | Runtime | Deterministic |
|---|---:|---:|---:|---:|:---:|
| pure_function_reorder | 365 | 63 | **82.7%** | 2.8ms | yes |
| import_reorder | 150 | 16 | **89.3%** | 33.5ms* | yes |
| extracted_helper (realistic) | 223 | 134 | 39.9% | 1.6ms | yes |
| large_rename (recursive, self-calls) | 242 | 65 | **73.1%** | 1.1ms | yes |
| formatting_only_change | 206 | 16 | **92.2%** | 2.1ms | yes |
| moved_class (reordered) | 255 | 32 | **87.5%** | 1.1ms | yes |
| method_extraction (realistic) | 338 | 171 | 49.4% | 12.0ms | yes |
| extracted_helper_verbatim (**positive control**) | 201 | 32 | **84.1%** | 2.3ms | yes |
| method_move_across_classes | 218 | 66 | **69.7%** | 7.4ms | yes |
| **Total** | **2198** | **595** | **72.9%** | | **9/9** |

\* First-call import overhead (`collect_import_statements_python`'s lazy import), not steady-state
cost — every other case after the first pays nothing for it.

### 4.1 The most important result is a negative one

QB-099 asked for "extracted function"/"inlined function" detection. The prototype implements it as an
**exact** check (a candidate helper's entire statement list must appear byte-for-byte, in order,
inside the original declaration, replaced by exactly one call statement — no partial match, no
similarity score). Three cases exercise it:

- **`extracted_helper_verbatim_control`** (deliberately the literal copy-paste case — statements moved
  into the new function completely unchanged): **detected correctly** as `extracted:`. This confirms
  the mechanism works when its precondition actually holds.
- **`extracted_helper`** and **`method_extraction`** (both modeled on how a real extraction usually
  looks — a trailing `order.total = round(total, 2)` assignment becomes a `return round(total, 2)`
  inside the new helper; `lines.append(...)` becomes `item_lines.append(...)` once the accumulator is
  renamed): **not detected**. Both degrade gracefully to an ordinary `modified` + `added` pair — no
  wrong answer, no crash, just a missed opportunity — but the extraction itself goes unrecognized.

This is not a prototype bug to fix; it is the expected, literature-confirmed result of QB-099's own
"no fuzzy similarity" constraint applied honestly. The tools that *do* reliably recognize real-world
extract-method refactors (RefactoringMiner, JDeodorant) do so precisely by scoring subtree similarity
against a threshold — the exact mechanism this ticket rules out. **Two of three extraction-shaped
benchmark cases were not recognized as extractions, and the one that was is not representative of how
extraction refactors actually get written.** Section 6 recommends descoping extract/inline detection
from a v1 rather than pretending exact-match will get there with more engineering effort.

### 4.2 A determinism finding that wasn't planned

Running the same benchmark from two different working directories (a plain temp directory outside any
git repo, vs. `docs/design/QB-099-prototype/` inside this repo, `core.autocrlf=true`) produced
**different baseline token counts** (2270 vs. 2198 — a ~3% swing) purely from `git diff --no-index`'s
own line-ending handling, while the **structural-diff totals were identical (595) in both
environments**, run to run, byte for byte. This is a small but real, unplanned demonstration of
exactly the property QB-099 is chasing: a pure-Python, self-contained structural comparison has no
dependency on the invoking environment's git config, whereas the line-oriented baseline it's being
compared against does.

### 4.3 Readability

Every non-`extracted`/`modified` line in the rendering is one sentence, self-contained, no reference
to a previous invocation — satisfying the 2026-07-31 Design Principle (`backlog.md`'s Vision section,
"independently understandable without requiring access to previous tool invocations") for free, since
nothing here depends on session state. `modified` entries still show a
real, scoped `difflib` diff — the prototype never claims equivalence for content that actually
changed; only genuinely `unchanged`/`renamed`-with-identical-body declarations are ever described
without their text shown.

---

## 5. Reuse audit

| Candidate | Reusable? | What's actually reusable |
|---|---|---|
| `ast_summarize/registry.py` (`get_analyzer`/`get_symbol_extractor`/language dispatch) | **Yes, directly, for routing** | Same "language name → callable" dict pattern; a structural-diff extractor would register as a fifth parallel family (`_STRUCTURAL_EXTRACTORS`), exactly like `_IMPORT_COLLAPSERS` was added for QB-096, using the same `EXTENSION_TO_LANGUAGE`/`is_language_available()`/`extra_for_language()` machinery unchanged. |
| `ast_summarize/python.py` (`_visit_module_body`/`_visit_class_body`, `collect_import_statements_python`) | **Yes, directly** | The prototype's own `extract_decls()` mirrors this traversal exactly and should become a third capability on the same functions (returning `(Symbol, ast.AST)` pairs) rather than a parallel copy — see §3's own note. `collect_import_statements_python()` is called as-is, unmodified, for import-only detection. |
| tree-sitter integration (`javascript.py`/`typescript.py`/`go.py`/`java.py`/`rust.py`/`csharp.py`) | **Partially, per-language follow-on work** | Every non-Python language already has its own parse + declaration-extraction logic (`extract_symbols_*`). The canonicalization/matching *algorithm* in `structural_diff.py` is language-agnostic by construction (it only needs "give me this declaration's own subtree, comparable node-for-node across two trees, ignoring byte offsets") — tree-sitter nodes support an equivalent structural dump. Extending past Python means one new canonicalizer per language, not a redesign of the matcher. Real, but bounded, follow-on effort — not free. |
| `mask.py` / `StageHandler` / `ContentMask` | **No, not as the mechanism itself** | Same conclusion the repo-summarization investigation already reached for a different capability: this produces a *new* document, not a downgraded subset of one already-captured blob (Anti-Goal #18). `ContentMask` is still the right thing to run *afterward*, over the structural-diff's own rendered text, exactly like `repo_profile`'s render step does (§3.6 there). |
| `quor/filters/builtin/git.toml` (`preserve_patterns`, `max_tokens`) | **Yes, unchanged, downstream** | The structural-diff transform would replace the *input text* the existing `git-diff` filter pipeline receives for declaration-shaped changes; `preserve_patterns`/`collapse_unchanged_context`/`max_tokens` all still run on top, on the new, shorter text, same as today. |
| QB-013 (tee recovery cache) | **Yes, directly, and load-bearing for trust** | A `renamed: X -> Y (body unchanged)` line is an *assertion*, not a shown fact, the first time Quor has made that kind of claim about code content (QB-095/QB-096 both stayed byte-exact/fully lossless by construction). The existing `[full output: ...]` recovery link is exactly the mitigation Quor already has for "trust our summary, but here's the receipt" — no new mechanism needed, just leaning on the one that already exists. |

---

## 6. Effort, risks, recommendation

**Estimated effort:** **Large** (a week or more) for a correct Python-only v1 — hierarchical (not
flat two-pass) matching, the pre-`ContentMask` integration point, a minimum-declaration-size floor
(see risk below), and a real test/benchmark suite following QB-095/QB-096/QB-097/QB-098's own
established bar (empty-input, no-match, PROTECT-survives, determinism, token-cost-gate-in-both-
directions coverage). **Medium, per additional language**, following the established registry
pattern, once the Python matcher's design is proven — the parsing/extraction infrastructure already
exists for every tree-sitter language `ast_summarize` supports today.

**Risks:**

- **Extract/inline detection cannot be built to this ticket's own constraints** (§4.1) — must be
  explicitly descoped from v1, not attempted and shipped half-working. This directly narrows
  QB-099's original wish list from eight recognized patterns to six (rename, move, reorder,
  formatting-only, import-only, plus a same-qualname "modified" fallback for everything else) — a
  real, evidence-backed scope cut, not an oversight.
- **Coincidental exact matches on trivial/boilerplate declarations.** Two unrelated one-line
  `__repr__`s, or two near-empty stub methods, can be genuinely, deterministically AST-identical
  without being "the same declaration that moved" in any meaningful sense — a real correctness/trust
  risk this investigation didn't need a benchmark case to surface, just inspection of the matching
  rule. Mitigation: a minimum-declaration-size floor before treating a hash match as a reportable
  correspondence at all, the same shape of guard QB-096 already added
  (`_MIN_ENTRIES_TO_COLLAPSE = 2`) after real testing surfaced its own "technically true, not useful"
  edge case — precedent, not a new idea.
- **Assertion, not proof.** Unlike every prior QB-095/096/097/098 stage, a structural-diff rendering
  can say "body unchanged" without showing the body — a real, new category of trust cost for Quor,
  mitigated (not eliminated) by the existing tee-recovery link (§5). Worth naming plainly before
  building, not discovering after.
- **Flat-traversal reuse assumption was wrong** (§3.1) — hierarchical matching is more implementation
  work than "reuse `ast_summarize`'s existing shape as-is" assumed at ticket-writing time. Folded into
  the effort estimate above, not hidden.
- **Multi-language coverage is a real, bounded follow-on cost**, not included free by reusing the
  registry pattern (§5) — each language needs its own canonicalizer, mirroring the same
  per-language-module discipline `_IMPORT_COLLAPSERS`/`_SYMBOL_EXTRACTORS` already established.

**Decision: Proceed after redesign.**

Not "not worth implementing" — the benchmark evidence is genuinely strong (73–92% additional
reduction, deterministic across every one of 9 varied cases and across two different invoking
environments, zero new runtime dependencies, real reuse of existing parser infrastructure) on exactly
the class of change QB-041's own entry already named as unsolved (a reorder/rename/move is 100%
`+`/`-`-prefixed today, so no existing Quor stage can touch it at all). Not "proceed as-is" either —
two real bugs (§3.1) and one hard negative result (§4.1) surfaced during this investigation's own
prototype, none of which were visible before actually building and benchmarking it, all of which
change the shape of a production v1: hierarchical (not flat) matching, extract/inline explicitly out
of scope, a size floor against coincidental trivial matches, and an honest accounting that this is
Large effort for Python plus Medium per additional language — not the "prototype was most of the
work" outcome a first read of the reuse audit alone would have suggested.
