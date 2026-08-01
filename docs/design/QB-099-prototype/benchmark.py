"""QB-099 prototype benchmark: structural diff vs. `git diff -U3` baseline
across 9 synthetic refactor scenarios (the 7 QB-099 asked for, plus a
positive control and a dedicated cross-class method-move case — see
docs/design/QB-099-structural-diff-compression-investigation.md Section 4.1
for why). Standalone script — not part of the Quor package, not committed.
See structural_diff.py's own module docstring.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from structural_diff import classify_file, render, render_tokens  # noqa: E402

CASES: dict[str, tuple[str, str]] = {}


# ---------------------------------------------------------------------------
# 1. Pure function reorder
# ---------------------------------------------------------------------------
_funcs = {
    "validate_order": '''def validate_order(order):
    if not order.items:
        raise ValueError("empty order")
    if order.total < 0:
        raise ValueError("negative total")
    return True
''',
    "apply_discount": '''def apply_discount(order, pct):
    order.total = order.total * (1 - pct / 100)
    return order.total
''',
    "compute_tax": '''def compute_tax(order, rate):
    tax = order.total * rate
    order.total += tax
    return tax
''',
    "format_receipt": '''def format_receipt(order):
    lines = [f"Order #{order.id}"]
    for item in order.items:
        lines.append(f"  {item.name}: {item.price}")
    lines.append(f"Total: {order.total}")
    return "\\n".join(lines)
''',
    "send_confirmation": '''def send_confirmation(order, email):
    body = format_receipt(order)
    mailer.send(email, "Order confirmed", body)
    return True
''',
}
old_reorder = "\n".join(_funcs[k] for k in ["validate_order", "apply_discount", "compute_tax", "format_receipt", "send_confirmation"])
new_reorder = "\n".join(_funcs[k] for k in ["send_confirmation", "compute_tax", "validate_order", "format_receipt", "apply_discount"])
CASES["pure_function_reorder"] = (old_reorder, new_reorder)

# ---------------------------------------------------------------------------
# 2. Import reorder
# ---------------------------------------------------------------------------
old_imports = """import sys
import os
import json
from collections import OrderedDict
from typing import Any, Optional
import re
from pathlib import Path

def load_config(path):
    with open(path) as f:
        return json.load(f)
"""
new_imports = """import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional
from collections import OrderedDict

def load_config(path):
    with open(path) as f:
        return json.load(f)
"""
CASES["import_reorder"] = (old_imports, new_imports)

# ---------------------------------------------------------------------------
# 3. Extracted helper (top-level function)
# ---------------------------------------------------------------------------
old_extract = '''def process_order(order):
    if not order.items:
        raise ValueError("empty order")
    subtotal = sum(item.price * item.qty for item in order.items)
    discount = subtotal * order.discount_pct / 100
    subtotal -= discount
    tax = subtotal * order.tax_rate
    total = subtotal + tax
    order.total = round(total, 2)
    order.status = "processed"
    return order.total


def cancel_order(order):
    order.status = "cancelled"
    return order
'''
new_extract = '''def process_order(order):
    if not order.items:
        raise ValueError("empty order")
    order.total = compute_total(order)
    order.status = "processed"
    return order.total


def compute_total(order):
    subtotal = sum(item.price * item.qty for item in order.items)
    discount = subtotal * order.discount_pct / 100
    subtotal -= discount
    tax = subtotal * order.tax_rate
    total = subtotal + tax
    return round(total, 2)


def cancel_order(order):
    order.status = "cancelled"
    return order
'''
CASES["extracted_helper"] = (old_extract, new_extract)

# ---------------------------------------------------------------------------
# 4. Large rename (recursive function, self-calls included)
# ---------------------------------------------------------------------------
old_rename = '''def calculate_fibonacci_sequence(n, memo=None):
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
new_rename = '''def fib_memo(n, memo=None):
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
CASES["large_rename"] = (old_rename, new_rename)

# ---------------------------------------------------------------------------
# 5. Formatting-only change
# ---------------------------------------------------------------------------
old_fmt = '''def build_url(base, path, params):
    url = base.rstrip('/') + '/' + path.lstrip('/')
    if params:
        query = '&'.join(f"{k}={v}" for k, v in params.items())
        url += '?' + query
    return url

def is_valid_url(url):
    return url.startswith('http://') or url.startswith('https://')
'''
new_fmt = '''def build_url(base, path, params):
    url = base.rstrip("/") + "/" + path.lstrip("/")

    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url += "?" + query
    return url


def is_valid_url(url):
    return url.startswith("http://") or url.startswith("https://")
'''
CASES["formatting_only_change"] = (old_fmt, new_fmt)

# ---------------------------------------------------------------------------
# 6. Moved class
# ---------------------------------------------------------------------------
class_a = '''class OrderValidator:
    def __init__(self, rules):
        self.rules = rules

    def validate(self, order):
        for rule in self.rules:
            if not rule(order):
                return False
        return True
'''
class_b = '''class ReceiptFormatter:
    def __init__(self, currency="USD"):
        self.currency = currency

    def format(self, order):
        return f"{order.total} {self.currency}"
'''
helper_fn = '''def make_default_validator():
    return OrderValidator([lambda o: bool(o.items)])
'''
old_moved_class = class_a + "\n\n" + class_b + "\n\n" + helper_fn
new_moved_class = class_b + "\n\n" + helper_fn + "\n\n" + class_a
CASES["moved_class"] = (old_moved_class, new_moved_class)

# ---------------------------------------------------------------------------
# 7. Method extraction
# ---------------------------------------------------------------------------
old_method_extract = '''class InvoiceBuilder:
    def __init__(self, order):
        self.order = order

    def build(self):
        lines = []
        lines.append(f"Invoice for order {self.order.id}")
        subtotal = 0
        for item in self.order.items:
            line_total = item.price * item.qty
            subtotal += line_total
            lines.append(f"{item.name} x{item.qty}: {line_total}")
        tax = subtotal * self.order.tax_rate
        total = subtotal + tax
        lines.append(f"Subtotal: {subtotal}")
        lines.append(f"Tax: {tax}")
        lines.append(f"Total: {total}")
        return "\\n".join(lines)

    def as_dict(self):
        return {"order_id": self.order.id}
'''
new_method_extract = '''class InvoiceBuilder:
    def __init__(self, order):
        self.order = order

    def build(self):
        lines = []
        lines.append(f"Invoice for order {self.order.id}")
        subtotal, item_lines = self._line_items()
        lines.extend(item_lines)
        tax = subtotal * self.order.tax_rate
        total = subtotal + tax
        lines.append(f"Subtotal: {subtotal}")
        lines.append(f"Tax: {tax}")
        lines.append(f"Total: {total}")
        return "\\n".join(lines)

    def _line_items(self):
        subtotal = 0
        item_lines = []
        for item in self.order.items:
            line_total = item.price * item.qty
            subtotal += line_total
            item_lines.append(f"{item.name} x{item.qty}: {line_total}")
        return subtotal, item_lines

    def as_dict(self):
        return {"order_id": self.order.id}
'''
CASES["method_extraction"] = (old_method_extract, new_method_extract)

# ---------------------------------------------------------------------------
# 8. Extracted helper, verbatim copy-paste (positive control — the one shape
#    exact-match extraction detection can actually catch: the lifted
#    statements are copied unchanged, not adapted).
# ---------------------------------------------------------------------------
old_extract_verbatim = '''def process_order(order):
    if not order.items:
        raise ValueError("empty order")
    subtotal = sum(item.price * item.qty for item in order.items)
    discount = subtotal * order.discount_pct / 100
    subtotal -= discount
    order.total = subtotal
    order.status = "processed"
    return order.total


def cancel_order(order):
    order.status = "cancelled"
    return order
'''
new_extract_verbatim = '''def process_order(order):
    if not order.items:
        raise ValueError("empty order")
    compute_discounted_subtotal(order)
    order.status = "processed"
    return order.total


def compute_discounted_subtotal(order):
    subtotal = sum(item.price * item.qty for item in order.items)
    discount = subtotal * order.discount_pct / 100
    subtotal -= discount
    order.total = subtotal


def cancel_order(order):
    order.status = "cancelled"
    return order
'''
CASES["extracted_helper_verbatim_control"] = (old_extract_verbatim, new_extract_verbatim)

# ---------------------------------------------------------------------------
# 9. Method move — a method relocated from one class to another, unchanged
#    (the "moved_class" case above only exercises the "reordered" path,
#    since both classes stay at module scope; this exercises the "moved"
#    path — different parent, same full-hash — the ticket's own "method
#    move" example).
# ---------------------------------------------------------------------------
old_method_move = '''class LegacyExporter:
    def __init__(self, records):
        self.records = records

    def to_csv(self):
        rows = [",".join(str(v) for v in r.values()) for r in self.records]
        return "\\n".join(rows)

    def to_json(self):
        import json
        return json.dumps(self.records)


class ReportBuilder:
    def __init__(self, title):
        self.title = title
'''
new_method_move = '''class LegacyExporter:
    def __init__(self, records):
        self.records = records

    def to_json(self):
        import json
        return json.dumps(self.records)


class ReportBuilder:
    def __init__(self, title):
        self.title = title

    def to_csv(self):
        rows = [",".join(str(v) for v in r.values()) for r in self.records]
        return "\\n".join(rows)
'''
CASES["method_move_across_classes"] = (old_method_move, new_method_move)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def git_diff_tokens(old_src: str, new_src: str) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as td:
        old_path = Path(td) / "a.py"
        new_path = Path(td) / "b.py"
        old_path.write_text(old_src, encoding="utf-8")
        new_path.write_text(new_src, encoding="utf-8")
        result = subprocess.run(
            ["git", "diff", "--no-index", "--no-color", "-U3", str(old_path), str(new_path)],
            capture_output=True, text=True,
        )
        text = result.stdout
        return render_tokens(text), text


def run():
    print(f"{'case':28s} {'baseline_tok':>13s} {'structural_tok':>15s} {'reduction':>10s} {'runtime_ms':>11s} {'deterministic':>14s}")
    print("-" * 100)
    totals = {"baseline": 0, "structural": 0}
    for name, (old_src, new_src) in CASES.items():
        t0 = time.perf_counter()
        summary1 = classify_file(old_src, new_src)
        rendered1 = render(summary1)
        t1 = time.perf_counter()
        summary2 = classify_file(old_src, new_src)
        rendered2 = render(summary2)
        deterministic = rendered1 == rendered2

        baseline_tok, baseline_text = git_diff_tokens(old_src, new_src)
        structural_tok = render_tokens(rendered1)
        reduction = 1 - (structural_tok / baseline_tok) if baseline_tok else 0.0
        runtime_ms = (t1 - t0) * 1000

        totals["baseline"] += baseline_tok
        totals["structural"] += structural_tok

        print(f"{name:28s} {baseline_tok:13d} {structural_tok:15d} {reduction:9.1%} {runtime_ms:10.2f}ms {str(deterministic):>14s}")
        print(f"  -> {rendered1.replace(chr(10), chr(10) + '     ')}")
        print()

    overall = 1 - (totals["structural"] / totals["baseline"]) if totals["baseline"] else 0.0
    print("-" * 100)
    print(f"TOTAL{'':23s} {totals['baseline']:13d} {totals['structural']:15d} {overall:9.1%}")


if __name__ == "__main__":
    run()
