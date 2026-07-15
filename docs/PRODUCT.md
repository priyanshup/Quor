# Quor — Product Vision

This document is Quor's single source of truth for *why* the product exists and *how* decisions
about it should be made. If a feature request, a design choice, or a roadmap item conflicts with
something written here, that's a signal to stop and resolve the conflict — not to quietly override
this document.

---

## 1. Vision

**In plain terms:** AI coding assistants read a lot of stuff — command output, files, logs — and
most of it is filler. Quor strips the filler out before it reaches the assistant, so the assistant
spends its limited attention on what actually matters.

**Why Quor exists.** Every AI coding assistant works inside a fixed-size context window, and every
token in that window costs money and attention. A large share of what gets fed into that window
carries no real information: hundreds of passing test lines, an unchanged `git status`, repeated
build warnings, a document's page furniture. That's cost paid on every single turn, and it crowds
out the one line that actually mattered — the failing assertion, the changed diff hunk.

**The problem it solves.** Nobody was filtering that noise out in a way that's safe, transparent,
and doesn't depend on trusting another model to summarize things correctly. Quor does it
deterministically: same input, same output, always — with the true original just one recovery link
away.

**Vision statement:** *Maximum practical token reduction, without ever compromising an AI
assistant's ability to correctly finish the task in front of it.*

---

## 2. Mission

**In plain terms:** over the next few years, Quor wants to become the thing that sits quietly
between every AI coding assistant and everything it reads — not just Claude Code, not just command
output — cutting waste automatically, everywhere, without anyone having to think about it.

Over the next few years, Quor is working to:

- Prove that "maximum practical token reduction" is real and measurable, not a slogan — by
  tightening the loop between compression changes and evidence that task success is preserved.
- Extend deterministic compression from single command/file reads to the whole session — noticing
  when the same file, error, or output has already been shown once, instead of re-paying for it
  every time.
- Support the AI coding assistants developers actually use day to day, not just one.
- Stay something any developer, on any machine — including a locked-down corporate laptop — can
  install and trust in minutes.

---

## 3. Product Philosophy

**In plain terms:** these are the beliefs that don't change even as features come and go. If a new
idea conflicts with one of these, the idea loses by default.

- **Maximum practical token reduction.** The goal is not "avoid ever removing anything that might
  matter" — it's removing as much low-value content as possible while an assistant can still do the
  job correctly. Being maximally cautious is not the same as being maximally useful.
- **Preserve task success over raw compression numbers.** A compression mode that raises the
  headline reduction percentage while quietly making the assistant fail the task more often is not
  a win. Task success is the real metric; the reduction percentage is a means to it, not the end.
- **Deterministic algorithms first.** No LLM calls, no ML models, no randomness in the filtering
  path. The same input always produces the same output, and every decision can be explained and
  audited — this is what makes Quor trustworthy enough to sit in front of every command.
- **Corporate-machine friendly.** Quor has to install and run cleanly on a locked-down Windows
  laptop with restrictive endpoint policies — no compiler toolchain, no admin rights, no blocked
  launcher stubs. If it doesn't work there, it doesn't work for a large share of the people who need
  it most.
- **Privacy-first (local execution).** Nothing Quor does requires sending code, output, or
  documents anywhere. All filtering happens locally, on the user's own machine.
- **Incremental improvement backed by measurement.** Quor moves in small, verifiable steps, each
  checked against a committed benchmark suite, rather than large speculative rewrites.
- **Evidence over assumptions.** Claims about what Quor does — what's shipped, what's safe, what's
  effective — are checked against real release history and real measurement, not restated from
  memory. When a document and reality disagree, reality wins and the document gets corrected.

---

## 4. Product Goals

**In plain terms:** some goals are the reason Quor exists; others are worth doing but shouldn't
come at the expense of the primary ones.

### Primary goals

- Reduce the token cost of running an AI coding assistant, measurably and in real usage — not just
  on a benchmark corpus.
- Preserve AI task success — the assistant must still be able to correctly finish the work it was
  given, using compressed context.
- Increase measurable, real-world token savings over time, tracked against a committed baseline so
  regressions are caught automatically.

### Secondary goals

- Improve developer productivity by removing noise a human would otherwise have to scroll past too.
- Support multiple AI coding assistants beyond Claude Code, once the core is proven.
- Expand language and format support beyond today's Python/JavaScript/TypeScript/TSX and
  Markdown/text/DOCX/PDF coverage.

---

## 5. Non-goals

**In plain terms:** these are things Quor is deliberately choosing not to become, even if they'd be
tempting to bolt on.

- **Not an AI coding assistant.** Quor doesn't write code, answer questions, or make suggestions —
  it only changes what an existing assistant is shown.
- **Not a code formatter.** Quor never rewrites, reformats, or changes a user's actual source files.
- **Not an LLM.** No model calls, no learned behavior, no non-deterministic output, anywhere in the
  filtering path.
- **Not a cloud service.** Quor runs locally; it is not a hosted API, and it does not require
  network access to function.
- **Not a code optimizer.** Quor has no opinion on code quality, performance, or style — it
  compresses what's *read*, not what's *written*.

---

## 6. Product Principles

**In plain terms:** this is the checklist-behind-the-checklist — how the team should actually decide
what to build and how to build it.

- Every feature must have measurable value — if it can't be shown to move a real metric, it's not
  ready to build.
- Benchmark before and after every change that touches compression behavior.
- Prefer small, iterative improvements over risky, large rewrites.
- Safety by default — nothing is ever silently and permanently lost; a recovery path always exists.
- Aggressive compression is only acceptable when task success is demonstrably preserved at that
  aggressiveness level.
- Real-world usage matters more than synthetic benchmarks — the benchmark suite exists to prevent
  regressions, not to define success on its own.

---

## 7. Success Metrics

**In plain terms:** this is how Quor knows it's actually working, not just looking like it's
working.

- **Practical token reduction** — real, measured savings across actual usage, not just the
  benchmark corpus.
- **Task success** — whether the assistant still completes the coding task correctly when working
  from compressed context, at the same rate as uncompressed.
- **Compression quality** — whether the information a task genuinely needed survived compression,
  not just whether the output got shorter.
- **Benchmark improvements** — the committed benchmark suite trending better over time, with no
  regressions slipping through.
- **Real-world usage** — evidence that Quor is actually being used in real, multi-hour sessions, not
  just installed once.
- **Reliability** — Quor never blocks a command, never changes its behavior or exit code, and always
  fails open if a filter breaks.
- **Ease of adoption** — how quickly a new user gets from installation to a healthy, working setup,
  including on constrained corporate machines.

---

## 8. Long-term Vision

**In plain terms:** where Quor is headed once the fundamentals are proven — this describes the
destination, not how to build it.

- **Better language understanding** — deeper, structure-aware compression across more programming
  languages, not just the ones supported today.
- **Better context optimization** — moving beyond compressing one command or file at a time, toward
  understanding what's actually useful across an entire working session.
- **Adaptive compression** — the ability to compress more or less aggressively depending on what a
  given task actually needs, without ever guessing recklessly.
- **Telemetry-driven improvement** — real usage data, gathered with the user's trust intact, shaping
  what gets improved next.
- **Broader AI assistant support** — the same trustworthy compression core, available to whichever
  AI coding assistant a developer has chosen to use.

---

## 9. Decision Checklist

**In plain terms:** before building anything, ask these questions. If the honest answer to several
of them is "no," the feature isn't ready yet.

- Does this increase practical token reduction?
- Can the impact be measured, before and after?
- Does it preserve task success — will the assistant still get the job done correctly?
- Is it deterministic — same input, same output, every time?
- Is it maintainable — can it be explained, audited, and safely changed later?
- Does it work on corporate machines, with no admin rights and restrictive endpoint policies?
- Is there a smaller slice of this that can be built and proven first?
