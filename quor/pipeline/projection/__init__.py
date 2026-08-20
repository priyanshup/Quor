"""QB-119: Tool Payload Projection.

Deterministic condensation of raw tool-call payloads (JSON API/command
responses, CLI log output) before they enter the ContentMask pipeline —
see `json_projector.py` and `log_projector.py`'s own module docstrings for
why each needs to exist outside the ContentMask stage system.
"""

from __future__ import annotations
