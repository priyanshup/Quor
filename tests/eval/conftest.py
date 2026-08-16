"""Prevent pytest's default `test_*.py` discovery from collecting
`fixtures/` as real tests.

`fixtures/fix_type_error/test_calc.py` is task fixture data — a behavioral
test deliberately run *against a buggy file* by checker.py/verify.py inside
an isolated scratch copy, where failing is the expected, correct outcome
for the pristine (unfixed) fixture. Collected by the outer `pytest tests/`
sweep, it looks like a real, failing project test instead of what it
actually is.
"""

from __future__ import annotations

collect_ignore = ["fixtures"]
