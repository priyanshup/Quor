# Security Policy

## Supported Versions

Quor is currently pre-1.0 (Internal Alpha). Only the latest released
version receives security fixes; there is no long-term-support branch yet.

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |
| < 0.1   | No        |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Instead, report privately via one of:

- [GitHub Security Advisories](https://github.com/priyanshup/Quor/security/advisories/new) for this repository (preferred).
- Direct email to the maintainer (see the repository's commit history / GitHub profile for contact details).

Please include:
1. A description of the vulnerability and its potential impact.
2. Steps to reproduce, including a minimal example if possible.
3. The Quor version (`quor --version`) and Python version affected.

We aim to acknowledge reports within 5 business days. As a single-maintainer,
pre-1.0 project there is no formal SLA yet, but security reports are
prioritized over other issues.

## Scope

Quor's design already limits its own attack surface in ways worth knowing
if you're evaluating whether something is a security issue:

- **No network calls in the hook path.** The Claude Code `PreToolUse` hook
  and command dispatch never make network requests — filtering is entirely
  local, rule-based text processing.
- **Fail-open, not fail-closed.** Every layer (filters, plugins, cache)
  degrades to the original, unfiltered command output on error rather than
  blocking execution or silently hiding data. This is a deliberate
  reliability property, not a vulnerability — Quor changes what's forwarded
  into an AI assistant's context, never what a command is allowed to do or
  what it returns to your own terminal.
- **Plugins run local, trusted Python code.** Third-party stages and
  plugins are ordinary Python packages you explicitly `pip install` — Quor
  does not sandbox them. Treat installing a Quor plugin the same as
  installing any other Python dependency: review the source, prefer known
  authors.

Things we do consider in-scope for a security report:
- A plugin or filter escaping the `PROTECT` decision's immutability guarantee.
- A way for filter TOML configuration (project-tier or otherwise) to trigger
  code execution beyond what `regex` pattern matching and the documented
  stage set allow.
- A way for the hook adapter to leak data to somewhere other than its
  declared stdout/tracking destinations.
- Path traversal or unsafe file handling in `quor init`, tracking storage,
  or the plugin cache.

## Disclosure

We follow coordinated disclosure: please give us a reasonable window to
investigate and release a fix before any public disclosure. We'll credit
reporters (unless you prefer to stay anonymous) in the fix's changelog entry.
