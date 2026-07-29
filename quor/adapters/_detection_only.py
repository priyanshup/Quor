"""Shared base for AgentAdapters with no confirmed hook capability (QB-069).

Researching Cursor, VS Code (Copilot agent mode), Windsurf (Cascade), Aider,
and Continue.dev's actual current integration mechanisms — live, against
each tool's own documentation, before writing any adapter code, per
ADR-036's mandatory pre-flight compatibility gate (§10.3) — found the same
shape of answer five times in a row, alongside Codex CLI (QB-068): **none
of these six tools has a confirmed way for an external hook to rewrite a
shell command before it runs, or replace a tool's output before the model
sees it.** The specific reason differs per tool (see each adapter module's
own docstring for its evidence):

- Cursor's `beforeShellExecution`/`beforeMCPExecution` hooks: documented
  allow/deny/ask only, no modify. No post-execution or post-read hook
  exists at all.
- VS Code's Copilot agent `PreToolUse`/`PostToolUse` hooks: documented
  allow/deny/prompt only ("no documented mechanism to rewrite/modify tool
  input"), and `PostToolUse` explicitly has "no documented support" for
  replacing a tool's result.
- Windsurf's Cascade `pre_run_command`/`pre_read_code` hooks: block-only
  (exit code 2, no structured response for modification); its
  `post_run_command`/`post_read_code` hooks are explicitly documented as
  observational only ("cannot modify command output or file content").
- Aider has no tool-call hook system at all — only `--lint-cmd`/`--test-cmd`,
  which wrap Aider's own auto-lint/auto-test feature, not a general
  command-interception point.
- Continue.dev's `config.yaml` reference has no hooks section whatsoever —
  its only extension points are MCP servers (agent-optional tool calls) and
  slash-command prompts.

Quor's `COMMAND_INTERCEPT`/`CONTENT_INTERCEPT` model fundamentally requires
one of: rewriting a command before it runs, or replacing content after a
tool produces it. Six independent adapters all landing on "neither is
achievable today" is exactly the kind of repeated, *confirmed* pattern that
justifies one shared implementation rather than six copies of the same
`supported_events = frozenset()` / advisory-doctor-checks / no-op-`install()`
boilerplate `CodexAdapter` (QB-068) originated — see ADR-041 for the full
decision record. Grouping the *shape* here does not imply the *reason* is
identical across tools — each concrete adapter's own module docstring
remains the authoritative source for its own finding, and re-verifying
against that tool's live documentation (not this summary) is required
before ever extending its `supported_events`.

**This class is not itself a registered adapter** — `agent_id`/
`display_name`/`limitation_reason` are unset here and must be provided by
every concrete subclass; `AdapterRegistry` never instantiates
`DetectionOnlyAdapter` directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from quor.adapters.base import (
    QUOR_ADAPTER_API_VERSION,
    AgentEvent,
    DoctorCheck,
    DoctorContext,
    InstallContext,
    InstallResult,
)

if TYPE_CHECKING:
    from quor.tracking.db import TrackingDB

_NO_EVENTS: frozenset[AgentEvent] = frozenset()


class DetectionOnlyAdapter:
    """Base `AgentAdapter` for a tool whose extension mechanism cannot
    (yet, confirmed) rewrite a command or replace tool output. Concrete
    subclasses supply `agent_id`/`display_name`/`limitation_reason` and
    override `_detect()`; every `AgentAdapter` method is implemented once,
    here.
    """

    agent_id: ClassVar[str]
    display_name: ClassVar[str]
    api_version: ClassVar[int] = QUOR_ADAPTER_API_VERSION

    limitation_reason: ClassVar[str]
    """One or two sentences: why this tool has no working compression hook
    today, specific to *this* tool's own researched finding (not a generic
    placeholder). Shown verbatim in `quor doctor`/`quor init` output."""

    @property
    def supported_events(self) -> frozenset[AgentEvent]:
        return _NO_EVENTS

    def handle_event(
        self, event: AgentEvent, raw_stdin: bytes, tracking: TrackingDB | None
    ) -> bytes | None:
        # No event is ever in supported_events, so callers never reach here
        # in practice — implemented for Protocol completeness/testability,
        # mirroring CodexAdapter's original rationale (QB-068).
        return None

    def install(self, ctx: InstallContext) -> InstallResult:
        return InstallResult(
            installed_paths=(),
            warnings=(
                f"{self.display_name} integration was not installed: "
                f"{self.limitation_reason} Quor will not install a hook it "
                "cannot verify actually compresses anything — this is a "
                "deliberate scope decision, not an error. Run `quor doctor` "
                "for current detection status.",
            ),
        )

    def doctor_checks(self, ctx: DoctorContext) -> list[DoctorCheck]:
        _detected, detail = self._detect()
        # Both checks are advisory (ok=True) regardless of detection state:
        # every adapter built on this base is a new, opt-in integration
        # with no working hook to install — its absence must never flip
        # `quor doctor`'s overall pass/fail for a user who only uses
        # Claude Code (or any other already-working adapter). Same
        # reasoning CodexAdapter established in QB-068, now shared.
        return [
            (f"{self.display_name} detected", True, detail),
            (
                f"{self.display_name} hook integration",
                True,
                f"not available — {self.limitation_reason} informational only.",
            ),
        ]

    def _detect(self) -> tuple[bool, str]:
        """Return `(detected, detail)`: whether this tool appears
        installed/configured on this machine, and a human-readable detail
        string for `quor doctor`. Must be a deterministic, local
        filesystem/PATH check only — no network calls, no heuristics.
        Every concrete subclass overrides this; the base implementation
        exists only so `DetectionOnlyAdapter` itself is not silently
        usable without one."""
        raise NotImplementedError
