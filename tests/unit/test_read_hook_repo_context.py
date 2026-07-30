"""QB-079: end-to-end "Repository Context" block coverage for the
`claude-read` hook — `quor/adapters/claude_read.py::_maybe_prepend_repo_context()`.

Driven through the real stdin -> stdout JSON contract, mirroring
`tests/unit/test_read_hook_ast_summarization.py`'s harness exactly. Every
test that needs a `file_intelligence.json` entry writes it directly via
`intel_store.save_file_intelligence()` (never a real `ensure_repo_
intelligence()` build) and `monkeypatch.chdir()`s into an isolated
`tmp_path` — `Path.cwd()` is exactly what `_maybe_prepend_repo_context()`
treats as the repository root (the same convention `FilterRegistry(
project_root=Path.cwd())` already uses a few lines above it in the same
file), and `tests/conftest.py`'s autouse `platformdirs` isolation fixture
already keeps the cache directory itself per-test.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch

import orjson
import pytest

from quor.adapters.claude_read import run_hook
from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.intel_model import FileIntelligenceEntry

# ---------------------------------------------------------------------------
# Helpers (mirrors test_read_hook_ast_summarization.py's own)
# ---------------------------------------------------------------------------


class _FakeStdout:
    def __init__(self) -> None:
        self.buffer: io.BytesIO = io.BytesIO()

    def write(self, s: str) -> int:
        return 0

    def flush(self) -> None:
        pass


def _read_payload(file_path: str, tool_response: str) -> dict:
    return {
        "tool_name": "Read",
        "tool_input": {"file_path": file_path},
        "tool_response": tool_response,
    }


def _run_hook(payload: dict) -> dict:
    raw = orjson.dumps(payload).decode("utf-8")
    fake_stdout = _FakeStdout()
    with (
        patch.object(sys, "stdin", io.StringIO(raw)),
        patch.object(sys, "stdout", fake_stdout),
    ):
        run_hook()
    fake_stdout.buffer.seek(0)
    return orjson.loads(fake_stdout.buffer.read())


_PYTHON_SOURCE = '''import os

DEFAULT_TIMEOUT = 30


def fetch_data(url, timeout=DEFAULT_TIMEOUT):
    """Fetch data from a URL."""
    response = make_request(url, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError("bad response")
    return response.json()


class Client:
    def __init__(self, base_url):
        self.base_url = base_url

    def get(self, path):
        full_url = self.base_url + path
        return fetch_data(full_url)
'''


def _write_matching_entry(root: Path, rel_path: str, **overrides: object) -> None:
    """Write `app.py` (or whatever `rel_path` names) to `root` with
    `_PYTHON_SOURCE`, then persist a `FileIntelligenceEntry` whose
    size/mtime_ns are taken from that real file's own `stat()` — so the
    hook's staleness check matches by construction unless a test
    deliberately overrides `size`/`mtime_ns`."""
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_PYTHON_SOURCE, encoding="utf-8")
    st = path.stat()
    fields: dict[str, object] = {
        "language": "python",
        "kind": "source",
        "importance": "High",
        "imports": 3,
        "imported_by": 61,
        "entry_point": False,
        "top_symbols": ["fetch_data", "Client"],
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
    }
    fields.update(overrides)
    intel_store.save_file_intelligence(root, {rel_path: FileIntelligenceEntry(**fields)})  # type: ignore[arg-type]


class TestRepositoryContextAppears:
    def test_block_appears_for_a_fresh_matching_entry(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _write_matching_entry(tmp_path, "app.py")

        result = _run_hook(_read_payload(str(tmp_path / "app.py"), _PYTHON_SOURCE))
        updated = result["hookSpecificOutput"].get("updatedToolOutput")

        assert isinstance(updated, str)
        assert "Repository Context (app.py):" in updated
        assert "Kind: Source" in updated
        assert "Language: python" in updated
        assert "Importance: High" in updated
        assert "Entry point: no" in updated
        assert "Defines: fetch_data, Client" in updated
        assert "Imports: 3 file(s) | Imported by: 61 file(s)" in updated
        # Still prepended after CONCISE_INSTRUCTION and before the compressed body.
        assert updated.index("Repository Context") < updated.index("def fetch_data")

    def test_block_reflects_entry_point_true(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _write_matching_entry(tmp_path, "app.py", entry_point=True)

        result = _run_hook(_read_payload(str(tmp_path / "app.py"), _PYTHON_SOURCE))
        updated = result["hookSpecificOutput"]["updatedToolOutput"]

        assert "Entry point: yes" in updated

    def test_no_top_symbols_renders_placeholder(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _write_matching_entry(tmp_path, "app.py", top_symbols=[])

        result = _run_hook(_read_payload(str(tmp_path / "app.py"), _PYTHON_SOURCE))
        updated = result["hookSpecificOutput"]["updatedToolOutput"]

        assert "Defines: (none)" in updated


class TestRepositoryContextOmitted:
    def test_omitted_when_no_file_intelligence_cache_at_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app.py").write_text(_PYTHON_SOURCE, encoding="utf-8")

        result = _run_hook(_read_payload(str(tmp_path / "app.py"), _PYTHON_SOURCE))
        updated = result["hookSpecificOutput"].get("updatedToolOutput")

        assert isinstance(updated, str)  # compression still happens
        assert "Repository Context" not in updated

    def test_omitted_when_no_entry_for_this_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _write_matching_entry(tmp_path, "other.py")
        (tmp_path / "app.py").write_text(_PYTHON_SOURCE, encoding="utf-8")

        result = _run_hook(_read_payload(str(tmp_path / "app.py"), _PYTHON_SOURCE))
        updated = result["hookSpecificOutput"].get("updatedToolOutput")

        assert isinstance(updated, str)
        assert "Repository Context" not in updated

    def test_omitted_when_stale_size_mismatch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _write_matching_entry(tmp_path, "app.py", size=999999)

        result = _run_hook(_read_payload(str(tmp_path / "app.py"), _PYTHON_SOURCE))
        updated = result["hookSpecificOutput"].get("updatedToolOutput")

        assert isinstance(updated, str)
        assert "Repository Context" not in updated

    def test_omitted_when_stale_mtime_mismatch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _write_matching_entry(tmp_path, "app.py", mtime_ns=1)

        result = _run_hook(_read_payload(str(tmp_path / "app.py"), _PYTHON_SOURCE))
        updated = result["hookSpecificOutput"].get("updatedToolOutput")

        assert isinstance(updated, str)
        assert "Repository Context" not in updated

    def test_omitted_for_markdown_even_with_an_incidental_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`.md` never routes through the source-code branch at all — the
        block must never attach there regardless of cache content."""
        monkeypatch.chdir(tmp_path)
        readme = tmp_path / "README.md"
        content = "# Title\n\n" + ("Some long paragraph of prose. " * 200) + "\n"
        readme.write_text(content, encoding="utf-8")
        st = readme.stat()
        intel_store.save_file_intelligence(
            tmp_path,
            {
                "README.md": FileIntelligenceEntry(
                    language="unknown", kind="source", size=st.st_size, mtime_ns=st.st_mtime_ns
                )
            },
        )

        result = _run_hook(_read_payload(str(readme), content))
        updated = result["hookSpecificOutput"].get("updatedToolOutput")

        assert updated is None or "Repository Context" not in updated

    def test_omitted_for_json_even_with_an_incidental_entry(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "config.json"
        content = orjson.dumps({"k": "v" * 500}).decode("utf-8")
        config.write_text(content, encoding="utf-8")
        st = config.stat()
        intel_store.save_file_intelligence(
            tmp_path,
            {
                "config.json": FileIntelligenceEntry(
                    language="unknown", kind="configuration", size=st.st_size, mtime_ns=st.st_mtime_ns
                )
            },
        )

        result = _run_hook(_read_payload(str(config), content))
        updated = result["hookSpecificOutput"].get("updatedToolOutput")

        assert updated is None or "Repository Context" not in updated

    def test_omitted_for_path_outside_repo_root_without_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _write_matching_entry(tmp_path, "app.py")

        elsewhere = tmp_path_factory.mktemp("elsewhere")
        outside_file = elsewhere / "app.py"
        outside_file.write_text(_PYTHON_SOURCE, encoding="utf-8")

        result = _run_hook(_read_payload(str(outside_file), _PYTHON_SOURCE))
        updated = result["hookSpecificOutput"].get("updatedToolOutput")

        assert isinstance(updated, str)  # never raised past the hook
        assert "Repository Context" not in updated

    def test_omitted_when_compression_itself_is_a_no_op(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty file compresses to itself — the whole hook stays a
        passthrough (no `updatedToolOutput` at all), so the Repository
        Context block, which only ever prepends onto genuinely changed
        output, never gets a chance to attach even with a matching entry."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "empty.py").write_text("", encoding="utf-8")
        intel_store.save_file_intelligence(
            tmp_path, {"empty.py": FileIntelligenceEntry(language="python", kind="source", size=0, mtime_ns=0)}
        )

        result = _run_hook(_read_payload(str(tmp_path / "empty.py"), ""))

        assert "updatedToolOutput" not in result["hookSpecificOutput"]
