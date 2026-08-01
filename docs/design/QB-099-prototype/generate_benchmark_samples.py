"""Generate git-diff benchmark sample files whose Python-file hunks are the
*real*, actual output of quor.pipeline.git_diff_enrich.enrich_git_diff() —
i.e. exactly what quor's git-structural-diff plugin (QB-099A/QB-099C) would
hand to FilterRegistry in real usage, not hand-typed text. Run once, offline;
output copied into tests/benchmarks/samples/git-diff/.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(r"C:\Users\PUSHPP02\OneDrive - Heineken International\Desktop\Workspace\Quor")
sys.path.insert(0, str(REPO))

from quor.pipeline.git_diff_enrich import enrich_git_diff  # noqa: E402


def make(cmd: str, raw_diff: str, old_by_path: dict[str, str], new_by_path: dict[str, str]) -> str:
    def git_show(ref, path, cwd):
        return old_by_path.get(path)

    def read_wt(path, cwd):
        return new_by_path.get(path)

    return enrich_git_diff(cmd, raw_diff, Path("."), git_show=git_show, read_working_tree=read_wt)


# ---------------------------------------------------------------------------
# 013: pure function reorder in one file, alongside an untouched second file
# ---------------------------------------------------------------------------
old_order_py = '''def validate_order(order):
    if not order.items:
        raise ValueError("empty order")
    if order.total < 0:
        raise ValueError("negative total")
    return True


def apply_discount(order, pct):
    order.total = order.total * (1 - pct / 100)
    return order.total


def compute_tax(order, rate):
    tax = order.total * rate
    order.total += tax
    return tax


def format_receipt(order):
    lines = [f"Order #{order.id}"]
    for item in order.items:
        lines.append(f"  {item.name}: {item.price}")
    lines.append(f"Total: {order.total}")
    return "\\n".join(lines)
'''

new_order_py = '''def compute_tax(order, rate):
    tax = order.total * rate
    order.total += tax
    return tax


def validate_order(order):
    if not order.items:
        raise ValueError("empty order")
    if order.total < 0:
        raise ValueError("negative total")
    return True


def format_receipt(order):
    lines = [f"Order #{order.id}"]
    for item in order.items:
        lines.append(f"  {item.name}: {item.price}")
    lines.append(f"Total: {order.total}")
    return "\\n".join(lines)


def apply_discount(order, pct):
    order.total = order.total * (1 - pct / 100)
    return order.total
'''

raw_013 = '''diff --git a/src/orders/processing.py b/src/orders/processing.py
index 3f8a1c2..9b7d4e1 100644
--- a/src/orders/processing.py
+++ b/src/orders/processing.py
@@ -1,23 +1,23 @@
-def validate_order(order):
-    if not order.items:
-        raise ValueError("empty order")
-    if order.total < 0:
-        raise ValueError("negative total")
-    return True
-
-
-def apply_discount(order, pct):
-    order.total = order.total * (1 - pct / 100)
-    return order.total
-
-
 def compute_tax(order, rate):
     tax = order.total * rate
     order.total += tax
     return tax


+def validate_order(order):
+    if not order.items:
+        raise ValueError("empty order")
+    if order.total < 0:
+        raise ValueError("negative total")
+    return True
+
+
 def format_receipt(order):
     lines = [f"Order #{order.id}"]
     for item in order.items:
         lines.append(f"  {item.name}: {item.price}")
     lines.append(f"Total: {order.total}")
     return "\\n".join(lines)
+
+
+def apply_discount(order, pct):
+    order.total = order.total * (1 - pct / 100)
+    return order.total
diff --git a/src/orders/README.md b/src/orders/README.md
index 1a2b3c4..5d6e7f8 100644
--- a/src/orders/README.md
+++ b/src/orders/README.md
@@ -1,3 +1,3 @@
 # Orders module

-Handles order validation, discounting, and receipt formatting.
+Handles order validation, tax, discounting, and receipt formatting.
'''

out_013 = make(
    "git diff",
    raw_013,
    {"src/orders/processing.py": old_order_py},
    {"src/orders/processing.py": new_order_py},
)
Path(REPO / "tests/benchmarks/samples/git-diff/013_python_function_reorder.txt").write_text(out_013, encoding="utf-8")
print("=== 013 ===")
print(out_013)
print()

# ---------------------------------------------------------------------------
# 014: recursive rename (self-referential calls)
# ---------------------------------------------------------------------------
old_fib_py = '''def calculate_fibonacci_sequence(n, memo=None):
    """Compute the nth Fibonacci number with memoization."""
    if memo is None:
        memo = {}
    if n in memo:
        return memo[n]
    if n <= 1:
        return n
    result = calculate_fibonacci_sequence(n - 1, memo) + calculate_fibonacci_sequence(n - 2, memo)
    memo[n] = result
    return result


def print_fibonacci_table(limit):
    for i in range(limit):
        print(i, calculate_fibonacci_sequence(i))
'''

new_fib_py = '''def fib_memo(n, memo=None):
    """Compute the nth Fibonacci number with memoization."""
    if memo is None:
        memo = {}
    if n in memo:
        return memo[n]
    if n <= 1:
        return n
    result = fib_memo(n - 1, memo) + fib_memo(n - 2, memo)
    memo[n] = result
    return result


def print_fibonacci_table(limit):
    for i in range(limit):
        print(i, fib_memo(i))
'''

raw_014 = '''diff --git a/src/math/sequences.py b/src/math/sequences.py
index 4a5b6c7..8d9e0f1 100644
--- a/src/math/sequences.py
+++ b/src/math/sequences.py
@@ -1,15 +1,15 @@
-def calculate_fibonacci_sequence(n, memo=None):
+def fib_memo(n, memo=None):
     """Compute the nth Fibonacci number with memoization."""
     if memo is None:
         memo = {}
     if n in memo:
         return memo[n]
     if n <= 1:
         return n
-    result = calculate_fibonacci_sequence(n - 1, memo) + calculate_fibonacci_sequence(n - 2, memo)
+    result = fib_memo(n - 1, memo) + fib_memo(n - 2, memo)
     memo[n] = result
     return result


 def print_fibonacci_table(limit):
     for i in range(limit):
-        print(i, calculate_fibonacci_sequence(i))
+        print(i, fib_memo(i))
'''

out_014 = make(
    "git diff",
    raw_014,
    {"src/math/sequences.py": old_fib_py},
    {"src/math/sequences.py": new_fib_py},
)
Path(REPO / "tests/benchmarks/samples/git-diff/014_python_recursive_rename.txt").write_text(out_014, encoding="utf-8")
print("=== 014 ===")
print(out_014)
print()

# ---------------------------------------------------------------------------
# 015: method moved from one class to another (QB-099C)
# ---------------------------------------------------------------------------
old_export_py = '''class LegacyExporter:
    def __init__(self, records):
        self.records = records

    def to_csv(self):
        rows = [",".join(str(v) for v in r.values()) for r in self.records]
        return "\\n".join(rows)


class ReportBuilder:
    def __init__(self, title):
        self.title = title
'''

new_export_py = '''class LegacyExporter:
    def __init__(self, records):
        self.records = records


class ReportBuilder:
    def __init__(self, title):
        self.title = title

    def to_csv(self):
        rows = [",".join(str(v) for v in r.values()) for r in self.records]
        return "\\n".join(rows)
'''

raw_015 = '''diff --git a/src/reports/export.py b/src/reports/export.py
index 9f8e7d6..1c2b3a4 100644
--- a/src/reports/export.py
+++ b/src/reports/export.py
@@ -1,12 +1,12 @@
 class LegacyExporter:
     def __init__(self, records):
         self.records = records

-    def to_csv(self):
-        rows = [",".join(str(v) for v in r.values()) for r in self.records]
-        return "\\n".join(rows)
-

 class ReportBuilder:
     def __init__(self, title):
         self.title = title
+
+    def to_csv(self):
+        rows = [",".join(str(v) for v in r.values()) for r in self.records]
+        return "\\n".join(rows)
'''

out_015 = make(
    "git diff",
    raw_015,
    {"src/reports/export.py": old_export_py},
    {"src/reports/export.py": new_export_py},
)
Path(REPO / "tests/benchmarks/samples/git-diff/015_python_cross_class_method_move.txt").write_text(out_015, encoding="utf-8")
print("=== 015 ===")
print(out_015)
