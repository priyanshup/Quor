"""Ad-hoc parity check: run benchmark.py's 9 cases through the real,
shipped quor.pipeline.ast_summarize.structural_diff_python module instead
of the investigation's own standalone prototype, to confirm the production
port behaves the same (or better, given the two bug fixes it carries that
the prototype only patched superficially). Not a pytest suite — see
tests/unit/test_structural_diff_python.py for the real, committed tests.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from benchmark import CASES

sys.path.insert(0, str(Path(__file__).parents[2]))  # repo root, for `import quor`
from quor.pipeline.ast_summarize.structural_diff_python import (
    diff_python_files,
    render,
    render_tokens,
)


def git_diff_tokens(old_src: str, new_src: str) -> int:
    with tempfile.TemporaryDirectory() as td:
        old_path, new_path = Path(td) / "a.py", Path(td) / "b.py"
        old_path.write_text(old_src, encoding="utf-8")
        new_path.write_text(new_src, encoding="utf-8")
        result = subprocess.run(
            ["git", "diff", "--no-index", "--no-color", "-U3", str(old_path), str(new_path)],
            capture_output=True, text=True,
        )
        return render_tokens(result.stdout)


total_baseline = total_structural = 0
for name, (old_src, new_src) in CASES.items():
    result = diff_python_files(old_src, new_src)
    rendered = render(result, old_src, new_src)
    baseline = git_diff_tokens(old_src, new_src)
    structural = render_tokens(rendered)
    total_baseline += baseline
    total_structural += structural
    reduction = 1 - (structural / baseline) if baseline else 0.0
    print(f"{name:30s} baseline={baseline:4d} structural={structural:4d} reduction={reduction:6.1%}")
    print("  -> " + rendered.replace("\n", "\n     "))
    print()

overall = 1 - (total_structural / total_baseline) if total_baseline else 0.0
print(f"TOTAL baseline={total_baseline} structural={total_structural} reduction={overall:.1%}")
