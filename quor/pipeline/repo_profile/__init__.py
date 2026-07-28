"""Repository Context Profile (QB-061) — ``quor map``.

A new, parallel capability sitting *beside* the ContentMask compression
pipeline, not inside it (see ``docs/design/QB-061-repo-context-profile.md``
§4 for the full architectural rationale): it reads **many** files and
synthesizes a deterministic orientation document (languages, frameworks,
build system, package manager, entry points, ...) that never existed
verbatim anywhere in the repo. That is categorically different from what
``StageHandler``/``ContentMask`` are built to do (Anti-Goal #18), so this
package has no dependency on ``quor.pipeline.mask``/``quor.pipeline.engine``
at all — it is a sibling of the compression pipeline, not a stage within it.

Every fact this package produces is deterministic and evidence-cited: no
LLM call, no network access, no heuristic that cannot be verified by
re-running the same scan against the same repo state (Anti-Goal #2, #3,
#10). See ``profiler.build_profile()`` for the single public entry point.
"""

from __future__ import annotations
