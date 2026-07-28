"""Detector-rule subsystem for the Repository Context Profile (QB-061).

Structurally mirrors ``quor/filters/`` (three-tier TOML loading, git-tracked
trust check for project-local rules — see ``registry.py``/``loader.py``),
but with a different matching contract: ``FilterRegistry.find()`` is
first-match-wins (one command maps to one filter), whereas repository
detection is "match every applicable rule" (a repo can legitimately be
both a Flask app and a Docker container and use GitHub Actions — these are
not mutually exclusive categories the way filter dispatch is).
"""

from __future__ import annotations
