"""QB-048 fix_type_error task: verification driver.

Success requires BOTH the real type checker and the real behavioral test to
pass — mypy alone can be satisfied by a `# type: ignore` comment without
actually fixing the bug; pytest alone doesn't prove the fix is well-typed.
Run as a single subprocess (`Task.verify_command = [sys.executable,
"verify.py"]`) rather than widening the harness's Task/VerifyOutcome model
to a list of commands, since this task is the only one that currently needs
more than one check.
"""

from __future__ import annotations

import subprocess
import sys

mypy_result = subprocess.run(
    [sys.executable, "-m", "mypy", "calc.py"], capture_output=True, text=True
)
if mypy_result.returncode != 0:
    print(mypy_result.stdout)
    print(mypy_result.stderr, file=sys.stderr)
    print("[verify] mypy failed", file=sys.stderr)
    sys.exit(1)

pytest_result = subprocess.run(
    [sys.executable, "-m", "pytest", "test_calc.py", "-q"], capture_output=True, text=True
)
if pytest_result.returncode != 0:
    print(pytest_result.stdout)
    print(pytest_result.stderr, file=sys.stderr)
    print("[verify] pytest failed", file=sys.stderr)
    sys.exit(1)

print("[verify] mypy and pytest both passed")
sys.exit(0)
