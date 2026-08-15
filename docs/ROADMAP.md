# Quor Roadmap

Quor makes AI coding assistants cheaper and faster to work with by shrinking the command output,
files, and documents they read — without losing the information they actually need to do the job
correctly.

This page explains **where Quor is headed**, in plain language. For the full engineering backlog
(ticket-level detail, technical write-ups, exact status), see `backlog.md`. Ticket IDs are only
listed in the reference table at the very bottom, for anyone who needs to cross-reference the two
documents.

We just shipped a new internal dashboard that measures, for the first time, exactly which parts of
Quor are pulling their weight and which aren't — using both a test corpus and real usage data. That
evidence reshaped the priorities below: some ideas that looked good on paper turned out to matter
less in practice, and one problem (compressing git diffs better) turned out to matter a lot more
than expected.

---

## Recently shipped

*Items that were listed as active or upcoming work below as of this page's last full pass —
moved here now that they're done, rather than left to sit under "Now"/"Next" indefinitely.*

- **Stopped filters that make things bigger, not smaller.** The mypy/npm regression was root-caused
  (two dispatcher-level additions — the tee recovery footer and the concise-output nudge — were
  being added unconditionally, without checking they cost less than the filter saved) and fixed.
  mypy's real-usage compression is now positive and verified against live telemetry.
- **Turned real usage data into an ongoing early-warning system.** `quor gain --filters` and
  `quor doctor` now automatically flag any filter whose real compression has gone negative or
  near-zero, and show real-vs-benchmark divergence for every filter, on demand.
- **Extended language support for structure-aware compression.** Go, Rust, Java, and C# now get the
  same signature-preserved, body-compressed treatment Python/JavaScript/TypeScript already had.
- **Compressed configuration files.** YAML, JSON, and TOML files (including common lockfiles) now
  get structure-aware compression; `.env` and `.ini` files get comment/blank-line stripping without
  ever touching a value.
- **Built the release-tracking half of the "benchmark suite that reflects real usage" item below.**
  The benchmark corpus now has a release-over-release trend view, and a workflow that turns the
  early-warning system above into concrete nominations for new, more realistic benchmark samples.
  The other half of that item — actually sampling real, anonymized usage into the corpus — remains
  open; see that item's own updated description below for why it's tracked separately now.

---

## Now

*What we're actively working on next — scoped, evidenced, and ready to build.*

### Compress git diffs much more aggressively

**Layman explanation:** When Claude looks at a code change (a "diff"), Quor currently plays it very
safe — it barely touches the diff at all, because it doesn't want to accidentally hide an edit. As a
result, diffs are one of the least-compressed things Quor handles today, even though nearly every
coding session is full of them.

**Why it matters:** Real usage data shows diffs account for nearly half of every token Quor has ever
saved on real projects — and they're still only compressing at roughly a third of the rate similar
git commands achieve. This is the single biggest, most evidenced opportunity in the entire backlog.

**Expected impact:** Projected to meaningfully increase Quor's total measured savings from one
change alone — more than any other single item on this roadmap. The approach keeps every actual
line of added/removed code fully intact; it only gets smarter about the unchanged, repetitive, or
generated "noise" surrounding those edits.

---

### Make Quor explain itself better

**Layman explanation:** Quor already has a way to show exactly what it changed and why for a given
command. This adds a "before vs. after token count" view to that explanation, and a way to preview
what a more aggressive compression setting would do before turning it on.

**Why it matters:** As Quor compresses more aggressively (see below), it becomes more important that
users and the AI itself can understand — and double-check — exactly what changed and why.

**Expected impact:** No direct token savings; this is a trust and transparency investment, and a
relatively quick one to deliver.

---

### Let users choose how aggressive compression should be

**Layman explanation:** Today Quor has one setting: play it safe, never touch anything it's
identified as important, even if that means barely compressing certain content. This adds a choice
— stay safe (today's behavior, still the default), compress a bit further into "protected" content
when confidence is high, or prioritize squeezing tokens hard and accept some risk that a rarely
needed detail might get trimmed (the AI can always ask again if it needs it back).

**Why it matters:** This is the feature that turns "maximum practical token reduction" from a stated
goal into something a user can actually opt into, deliberately and knowingly.

**Expected impact:** Potentially high — but rollout is intentionally gated behind proving compressed
output doesn't hurt the AI's ability to actually finish tasks correctly (see the task-success
evaluation work in **Next**, below).

---

### Teach Quor to tune itself automatically

**Layman explanation:** Right now, how aggressively each part of Quor compresses is a fixed choice
someone made once in a config file. This explores having Quor automatically lean harder into the
parts that are proven to work well, and automatically ease off the parts proven not to help — based
on real evidence, without a person having to notice and hand-tune it.

**Why it matters:** Flagged internally as one of the most important long-term directions for the
product — moving from hardcoded assumptions about what to compress toward decisions grounded in
measured, real-world outcomes.

**Expected impact:** Potentially significant over time, but this is still exploratory — the
continuous monitoring infrastructure it would build on now exists (see "Recently shipped" above),
but this item itself remains its own design pass, not yet scoped.

---

## Next

*High-value work that's well understood but waiting for the items above to land first.*

### Notice repetition across a whole coding session, not just one command at a time

**Layman explanation:** Every one of Quor's current tricks looks at one command's output in
isolation. But in a real coding session, the same file gets re-read, the same failing test gets
re-run, and the same error gets shown again and again. None of that repetition is caught today —
Quor treats every single call as if it's never seen anything like it before.

**Why it matters:** This is very likely the single largest remaining source of wasted tokens Quor
doesn't yet address — and the clearest expression of thinking about "the whole session" instead of
just "one command."

**Expected impact:** Potentially the biggest lever available to Quor long-term, but also the
riskiest and most architecturally ambitious change under consideration — it requires Quor to
remember things between calls for the first time, and needs strong proof it won't cause the AI to
act on stale information before it ships.

---

### Summarize repeated test failures instead of repeating them

**Layman explanation:** When one bug breaks many similar test cases (a common pattern), Quor should
recognize that they all stem from the same root cause and show it once with a count, instead of
repeating a near-identical failure message dozens of times.

**Why it matters:** Real usage shows test-output compression underperforming its test-case
expectations by roughly a third — this is a well-evidenced, specific gap.

**Expected impact:** Medium — a solid, contained win once scoped alongside the improved benchmark
work above.

---

### Compress more build and CI logs

**Layman explanation:** Quor already handles common build tools well. This extends similar treatment
to things like Docker build output and raw CI logs pasted in for troubleshooting.

**Why it matters:** These are common, noisy, and structurally similar to output Quor already
compresses well — a natural, low-risk extension.

**Expected impact:** Medium, though there's currently no strong evidence this is broken today — worth
revisiting once the improved benchmark corpus can confirm it.

---

### Track how Quor compares to competitors, continuously

**Layman explanation:** Quor's understanding of how it stacks up against similar tools comes from a
one-time research write-up. This would make that comparison an ongoing, automated check instead of a
snapshot frozen in time.

**Why it matters:** "Best in practice" needs an outside yardstick, not just an internal one — but
this is a positioning exercise, not a compression improvement in itself.

**Expected impact:** No direct token savings — this is about proving Quor's numbers hold up
externally, on an ongoing basis.

---

### Prove compressed output doesn't hurt the AI's ability to finish the job

**Layman explanation:** Every measurement Quor has today answers "how much smaller did this get?" —
none of them answer "could the AI still do the task correctly with the smaller version?" This builds
a small test suite that actually checks the second question.

**Why it matters:** This is the safety net that has to exist before Quor can responsibly ship more
aggressive compression modes or self-tuning behavior — it's what separates "aggressive" from
"reckless."

**Expected impact:** No direct token savings, but it's the prerequisite that makes several of the
higher-impact ideas above safe to turn on by default.

---

## Future

*Real, approved ideas — just deliberately lower priority, or waiting on other work to prove out
first.*

### Sample real usage into the benchmark corpus

**Layman explanation:** The benchmark test set is entirely hand-written today. This would add a
way to contribute real, anonymized examples of what Quor actually sees in practice — with a
person's explicit, informed consent — instead of relying only on examples someone wrote by hand.

**Why it matters:** Hand-written examples, however realistic, are still not a random sample of real
usage — a genuinely representative test set needs some real examples in it.

**Expected impact:** Would meaningfully sharpen every future measurement — but this specifically
requires its own privacy and legal review before any design work starts, not just an engineering
decision, since it means handling real command output rather than metadata about it. Intentionally
kept separate from — and behind — the measurement/tracking work already shipped (see "Recently
shipped" above), which needed no such review because it never touches real content at all.

---

### A safe way to try experimental, higher-risk compression ideas

**Layman explanation:** There are more ambitious compression ideas being explored (see below) that
are too experimental to build into Quor's core today. This would create a clearly-labeled,
opt-in-only "experimental" track so a promising idea could actually be tried without risking the
reliability everyone depends on.

**Why it matters:** Right now there's no defined path from "interesting idea" to "something a user
can actually try" — this would build that bridge.

**Expected impact:** No direct savings by itself; it's infrastructure that unlocks future
experimentation safely.

---

### Support more AI coding assistants (beyond Claude Code)

**Layman explanation:** Quor currently only works with Claude Code. Other tools in this space already
support multiple AI coding assistants (Cursor, GitHub Copilot, Gemini). This would bring Quor's
compression to those tools too.

**Why it matters:** Real long-term value and market reach — but each additional assistant is its own
multi-week engineering effort with its own integration quirks, and the deliberate call is to prove
Quor earns sustained real-world usage on what it already supports before expanding outward.

**Expected impact:** High long-term reach, intentionally sequenced after adoption is proven, not
before.

---

### Show new users what Quor would have saved them, retroactively

**Layman explanation:** A tool that scans a new user's past AI coding sessions and shows them,
concretely, how many tokens Quor would have saved if it had been running the whole time.

**Why it matters:** A genuinely good adoption/conversion tool — other tools in this space already
have something similar — but it doesn't differentiate Quor technically, it just helps sell what
already works.

**Expected impact:** No compression impact; a retention/adoption investment best made once there's an
actual user base to retain.

---

### Looking further ahead: ideas under investigation, not yet planned

Beyond the roadmap above, we're keeping an eye on a few more ambitious, higher-risk compression
techniques — using a small AI model to judge what's safe to trim, running multiple compression
passes back to back, representing content by its meaning rather than its structure, and similar
approaches. None of these are approved for building yet. Quor's reliability today comes from being
predictable and fully explainable, and every one of these ideas trades away some of that
predictability in exchange for potentially higher compression. That trade might be worth it in some
cases — it just hasn't been proven yet, which is exactly why these stay in the exploration pile
rather than on the roadmap above.

---

## Reference: initiative-to-ticket mapping

*For cross-referencing with `backlog.md` only.*

| Roadmap initiative | Backlog ticket(s) |
|---|---|
| Compress git diffs much more aggressively | QB-041, QB-055 |
| Stop filters that make things bigger, not smaller | QB-052 (shipped) |
| Sample real usage into the benchmark corpus / release-tracking half | QB-047 (Phase 1 shipped; real-content sampling still open) |
| Turn real usage data into an ongoing early-warning system | QB-054 (shipped) |
| Make Quor explain itself better | QB-049 |
| Let users choose how aggressive compression should be | QB-039 |
| Teach Quor to tune itself automatically | QB-053 |
| Support more programming languages | QB-046 (shipped) |
| Notice repetition across a whole coding session | QB-043 |
| Summarize repeated test failures instead of repeating them | QB-044 |
| Compress more build and CI logs | QB-045 |
| Compress configuration files | QB-040 (shipped) |
| Track how Quor compares to competitors, continuously | QB-042 |
| Prove compressed output doesn't hurt task success | QB-048 |
| A safe way to try experimental compression ideas | QB-050 |
| Support more AI coding assistants | QB-035 |
| Show new users what Quor would have saved them | QB-034 |
| Foundational analytics work behind this roadmap's re-prioritization | QB-051 (shipped) |
