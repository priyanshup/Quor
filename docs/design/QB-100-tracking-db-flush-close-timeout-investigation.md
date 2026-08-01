# Investigation: `TrackingDB.flush()`/`close()` Silent Timeout (QB-100)

> Status: **root-caused and fixed.** Triggered by a CI failure on the QB-099A/QB-099C PR
> (`tests/unit/test_tracking.py::TestReadTracking::test_unsupported_file_type_tracked`,
> `sqlite3.OperationalError: no such table: invocations`) that investigation confirmed was
> pre-existing and unrelated to that PR's own diff — recorded here as its own ticket per this
> project's "each fix gets its own entry" discipline, not folded into QB-099's.

---

## 1. Symptom

A single test near the very end of a full `pytest tests/` run (~2,000+ tests, ~5 minutes) failed
in CI with:

```
sqlite3.OperationalError: no such table: invocations
```

The same test passed reliably every time when run in isolation or as part of its own file locally.

## 2. Root cause

`TrackingDB` (`quor/tracking/db.py`) is a **non-blocking, background-threaded** SQLite writer —
`__init__` spawns a daemon thread and returns immediately; the actual `sqlite3.connect()` call and
`CREATE TABLE IF NOT EXISTS` (via `_apply_schema()`) happen only inside that background thread, the
first time it runs. `record()` only ever enqueues.

`flush()`/`close()` were the only way to wait for that background work to actually happen — and both
were **silent, unconfirmed, fixed-2.0-second waits**:

```python
def close(self, timeout: float = 2.0) -> None:
    self._queue.put(_STOP)
    self._thread.join(timeout=timeout)          # no check afterward

def flush(self, timeout: float = 2.0) -> None:
    done = threading.Event()
    self._queue.put(done)
    done.wait(timeout=timeout)                   # return value discarded
```

If the worker thread hadn't been scheduled to run at all within that window — not stuck, just not
yet given CPU time — both calls returned anyway, with no signal that anything was still pending.

**Why the window could be missed:** every `TrackingDB` instance spawns its own OS thread. This
repo's own test suite constructs 57+ instances directly (`test_tracking.py`: 29, `test_cli.py`: 21,
plus one each in six other files) and further instances indirectly via `get_tracking_db()`
(`doctor`/`explore`/`graph`/`map`/`repo`/`search`/`symbols` CLI commands). `close()`'s `join()` never
checks `is_alive()` afterward — a call whose own worker thread hadn't finished within 2s left that
thread **alive and unmonitored** for the rest of the single, long-running pytest process. As such
threads accumulate over a long run, they compete for OS scheduling time with every subsequently
created thread, including a brand-new, otherwise completely unrelated test's `TrackingDB`.

`test_unsupported_file_type_tracked` failed near the `98%` mark of the CI log — exactly where
accumulated thread pressure from earlier tests would be at its highest.

**Ruled out:**
- `_compress_read_output()` (`claude_read.py`) and `track_invocation()` (`db.py`) were traced fully:
  for this test's inputs, `track_invocation()` **was** called and `tracking.record(rec)` (a plain
  `queue.SimpleQueue.put()`) **did** succeed. The failure is entirely downstream, in persistence
  confirmation — not in "was tracking attempted" logic.
- `get_tracking_db()` is a plain factory, not a cached singleton.
- `tests/conftest.py`'s autouse `_isolate_platformdirs` fixture redirects every test to its own
  isolated file path — ruling out cross-test **file-lock** contention. It does not (and structurally
  cannot) isolate **thread count**, which is the actual mechanism here.
- The QB-099A/QB-099C PR touches neither `tracking/db.py` nor `claude_read.py`, and its own new
  tests construct zero `TrackingDB` instances (confirmed directly).

## 3. Direct reproduction

A standalone script constructed increasing numbers of `TrackingDB` instances, deliberately left
un-joined (simulating threads that missed their own `close()` window), then timed a fresh instance's
`flush()`+`close()` — mirroring the failing test's own pattern:

```
leaked_threads=    0  table_created=True   flush+close_elapsed=0.013s
leaked_threads=  500  table_created=True   flush+close_elapsed=0.792s
leaked_threads= 1000  table_created=True   flush+close_elapsed=3.106s
leaked_threads= 2000  table_created=False  flush+close_elapsed=4.006s
```

At sufficient thread pressure, the failure reproduces exactly — `"no such table: invocations"` —
from scheduling pressure alone, no changes to any tested logic.

## 4. Constraint the fix had to respect

`TrackingDB.__init__()` **must stay non-blocking.** `quor/__main__.py`'s own comment is explicit:

> "Opening a TrackingDB unconditionally here would add real per-invocation overhead to the hot
> COMMAND_INTERCEPT path that today's `claude` hook never pays."

And ADR-008 (`docs/final/DECISIONS.md`): *"Every pipeline result is written to SQLite (WAL mode,
background thread)... Neither write blocks the hook response."* Moving schema creation into a
synchronous `__init__` (the most direct-looking "fix") would have silently reintroduced exactly the
per-invocation latency this codebase deliberately engineered away. Rejected for this reason.

## 5. Fix

`quor/tracking/db.py`:
- Raised `flush()`/`close()`'s default timeout **2.0s → 10.0s** (`_STOP_WAIT_TIMEOUT_SECONDS`).
  `join()`/`Event.wait()` both return as soon as the real condition is satisfied, so this adds zero
  latency to the fast, uncontended path every real single-invocation `quor <command>` process runs
  under — it only extends how long a genuinely contended case is given before giving up.
- Both methods now **return `bool`** (`True` = genuinely caught up, `False` = timed out) instead of
  `None`, and **warn** (`warnings.warn`, this project's standing fail-open-but-visible convention) on
  `False` — a silently-discarded outcome becomes a loud one. No existing call site checks the return
  value (grepped directly), so this is purely additive/backward-compatible.
- `__init__` is untouched — still spawns a thread and returns immediately.

## 6. Verification

- Re-ran the same reproduction script against the fixed code: `table_created=True` at every thread
  count tested, including 2000 (previously the first failing point).
- 5 new deterministic regression tests (`TestFlushCloseTimeoutReporting`,
  `tests/unit/test_tracking.py`) — using a controlled `threading.Event` to block the worker thread,
  not real wall-clock/thread-count pressure, per this repo's own no-flaky-test convention.
- Full `tests/unit/test_tracking.py`: 121/121 (116 pre-existing + 5 new), unchanged.
- `ruff check` / `mypy` on `quor/tracking/db.py`: clean.
