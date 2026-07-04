"""Command classifier and rewriter for Quor.

Public API:
    classify_command(cmd) -> ClassificationResult
    rewrite_command(cmd)  -> str | None
"""

from __future__ import annotations

from dataclasses import dataclass

from quor.rewrite.invocation import get_quor_invocation
from quor.rewrite.lexer import Token, TokenKind, has_heredoc, parse_args, split_compound, tokenize
from quor.rewrite.rules import (
    cat_is_safe_to_rewrite,
    has_structured_output_flag,
    is_known_command,
    is_transparent_prefix,
    pipe_segment_is_safe,
)


@dataclass(frozen=True)
class ClassificationResult:
    should_rewrite: bool
    rewritten: str | None     # None when should_rewrite is False
    reason: str               # human-readable for quor explain
    rule_matched: str | None  # rule name for quor explain


def rewrite_command(cmd: str) -> str | None:
    """Return the rewritten command string, or None if command should pass through."""
    result = classify_command(cmd)
    return result.rewritten if result.should_rewrite else None


def classify_command(cmd: str) -> ClassificationResult:
    """Classify and optionally rewrite a shell command string."""
    cmd = cmd.strip()
    if not cmd:
        return ClassificationResult(
            should_rewrite=False,
            rewritten=None,
            reason="Empty command",
            rule_matched=None,
        )

    # --- Exclusion: heredoc ---
    if has_heredoc(cmd):
        return ClassificationResult(
            should_rewrite=False,
            rewritten=None,
            reason="Command contains heredoc — not rewritten",
            rule_matched="exclude:heredoc",
        )

    # --- Compound commands: split and rewrite each segment ---
    segments = split_compound(cmd)
    if len(segments) > 1:
        return _classify_compound(cmd, segments)

    # --- Check for pipes ---
    tokens = tokenize(cmd)
    pipe_positions = [i for i, t in enumerate(tokens) if t.kind == TokenKind.PIPE]
    if pipe_positions:
        return _classify_piped(cmd, tokens, pipe_positions)

    # --- Simple command ---
    return _classify_simple(cmd)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _classify_compound(
    original: str, segments: list[tuple[str, str | None]]
) -> ClassificationResult:
    """Rewrite each segment of a compound command independently."""
    rewritten_parts: list[str] = []

    for segment, operator in segments:
        seg_result = classify_command(segment)
        if not seg_result.should_rewrite:
            # One non-rewritable segment means we pass through that segment as-is
            rewritten_parts.append(segment)
        else:
            rewritten_parts.append(seg_result.rewritten or segment)

        if operator is not None:
            rewritten_parts.append(operator)

    rewritten = " ".join(rewritten_parts)
    changed = rewritten != original
    return ClassificationResult(
        should_rewrite=changed,
        rewritten=rewritten if changed else None,
        reason="Compound command: each segment classified independently",
        rule_matched="compound",
    )


def _classify_piped(
    original: str, tokens: list[Token], pipe_positions: list[int]
) -> ClassificationResult:
    """Handle pipe chains. Only rewrite the first segment; check pipe safety."""
    # Rebuild segments from token list split at pipes
    segments_raw: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok.kind == TokenKind.PIPE:
            segments_raw.append(current)
            current = []
        else:
            current.append(tok.value)
    segments_raw.append(current)

    if not segments_raw:
        return _passthrough(original, "Empty pipe chain")

    # Check if any pipe target is unsafe (xargs, awk, sed, ...)
    for pipe_seg in segments_raw[1:]:
        seg_str = " ".join(pipe_seg).strip()
        if not pipe_segment_is_safe(seg_str):
            cmd_word = seg_str.split()[0] if seg_str.split() else "unknown"
            return ClassificationResult(
                should_rewrite=False,
                rewritten=None,
                reason=f"Piped through '{cmd_word}' — not rewritten",
                rule_matched="exclude:pipe_incompatible",
            )

    # Rewrite only the first segment
    first_seg = " ".join(segments_raw[0]).strip()
    first_result = classify_command(first_seg)

    if not first_result.should_rewrite:
        return _passthrough(original, first_result.reason)

    # Reconstruct pipe chain with rewritten first segment
    rest_segs = [" ".join(s).strip() for s in segments_raw[1:]]
    rewritten = " | ".join([first_result.rewritten or first_seg, *rest_segs])
    return ClassificationResult(
        should_rewrite=True,
        rewritten=rewritten,
        reason="Pipe chain: first segment rewritten",
        rule_matched="pipe_first",
    )


def _classify_simple(cmd: str) -> ClassificationResult:
    """Classify a simple (non-compound, non-piped) command."""
    words = parse_args(cmd)
    if not words:
        return _passthrough(cmd, "Empty command after parsing")

    # --- Strip env-var assignments from the front ---
    env_words: list[str] = []
    for tok in tokenize(cmd):
        if tok.kind == TokenKind.ENV_ASSIGN:
            env_words.append(tok.value)
        else:
            break
    env_prefix = (" ".join(env_words) + " ") if env_words else ""
    remaining_words = words[len(env_words):]

    if not remaining_words:
        return _passthrough(cmd, "Only env assignments, no command")

    # --- Transparent prefix check (sudo, docker exec, etc.) ---
    transparent_prefix = ""
    match = is_transparent_prefix(remaining_words)
    if match:
        transparent_prefix, remaining_words = match
        transparent_prefix += " "

    if not remaining_words:
        return _passthrough(cmd, "No command after transparent prefix")

    base = remaining_words[0]
    args = remaining_words[1:]

    # --- Structured output exclusion ---
    if has_structured_output_flag(args):
        return ClassificationResult(
            should_rewrite=False,
            rewritten=None,
            reason=f"'{base}' uses structured output flag — not rewritten",
            rule_matched="exclude:structured_output",
        )

    # --- cat: only safe flags ---
    if base == "cat" and not cat_is_safe_to_rewrite(args):
        return ClassificationResult(
            should_rewrite=False,
            rewritten=None,
            reason="cat with non-display flags — not rewritten",
            rule_matched="exclude:cat_flags",
        )

    # --- Unknown command passthrough ---
    if not is_known_command(base, args):
        return _passthrough(cmd, f"Unknown command '{base}' — passthrough")

    # --- Build the rewritten command ---
    arg_str = (" " + " ".join(args)) if args else ""
    rewritten = f"{env_prefix}{transparent_prefix}{get_quor_invocation()} {base}{arg_str}"
    return ClassificationResult(
        should_rewrite=True,
        rewritten=rewritten,
        reason=f"Known command '{base}' → prepended with quor",
        rule_matched=f"rewrite:{base}",
    )


def _passthrough(cmd: str, reason: str) -> ClassificationResult:
    return ClassificationResult(
        should_rewrite=False,
        rewritten=None,
        reason=reason,
        rule_matched=None,
    )
