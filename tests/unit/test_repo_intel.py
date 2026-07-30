"""Unit tests for quor/pipeline/repo_profile/intel.py — QB-072's Automatic
Repository Intelligence orchestrator (`ensure_repo_intelligence()`).

Covers every scenario QB-072 calls out explicitly: first repository,
unchanged repository, file modified, file deleted, file renamed, rebuild
after a version change, corrupted cache, deterministic repeated runs, and
a performance-regression guard (asserted via parse call counts, not wall
clock — a flaky timing threshold would violate this repo's own testing
rules, see docs/final/CLAUDE.md's Rule 2).

Every test scans a `repo` fixture directory (`tmp_path / "repo"`), never
`tmp_path` itself — `tests/conftest.py`'s autouse fixture redirects
`platformdirs.user_data_dir("quor")` (this cache's own storage location)
to `tmp_path / "data" / "quor"`, a sibling of `repo`, not a descendant of
it. Scanning `tmp_path` directly would make the cache's own JSON files
show up as new untracked files in the *next* walk of the same repo,
falsely breaking the "unchanged repository" invariant every cache-hit/
determinism test here depends on."""

from __future__ import annotations

import subprocess
from pathlib import Path

import orjson
import pytest

import quor
from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.intel import ensure_repo_intelligence


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def _git_add_all(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)


def _make_repo(root: Path) -> None:
    _init_git_repo(root)
    (root / "a.py").write_text("def foo():\n    pass\n", encoding="utf-8")
    (root / "b.py").write_text("from a import foo\n\n\ndef bar():\n    foo()\n", encoding="utf-8")
    _git_add_all(root)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


class TestFirstTimeRepository:
    def test_onboards_and_builds_everything(self, repo: Path) -> None:
        _make_repo(repo)
        messages: list[str] = []

        result = ensure_repo_intelligence(repo, echo=messages.append)

        assert result.action == "onboarded"
        assert any("New repository detected" in m for m in messages)
        assert any("Scanning repository" in m for m in messages)
        assert any("Finished in" in m for m in messages)
        assert result.profile.root == repo.as_posix()
        assert {f.path for f in result.symbols.files} == {"a.py", "b.py"}
        assert result.graph.total_edges > 0
        assert intel_store.state_exists(repo)

    def test_persists_cache_files_to_disk(self, repo: Path) -> None:
        _make_repo(repo)
        ensure_repo_intelligence(repo)

        cache_dir = intel_store.cache_dir(repo)
        assert (cache_dir / "state.json").exists()
        assert (cache_dir / "profile.json").exists()
        assert (cache_dir / "symbol_facts.json").exists()
        assert (cache_dir / "graph_facts.json").exists()
        assert (cache_dir / "file_intelligence.json").exists()


class TestUnchangedRepository:
    def test_second_call_is_a_cache_hit(self, repo: Path) -> None:
        _make_repo(repo)
        ensure_repo_intelligence(repo)

        messages: list[str] = []
        result = ensure_repo_intelligence(repo, echo=messages.append)

        assert result.action == "cache_hit"
        assert result.diff is not None
        assert result.diff.is_empty
        assert messages == []  # no onboarding banner, no progress lines, on a true cache hit

    def test_cache_hit_reparses_nothing(self, repo: Path, monkeypatch) -> None:
        _make_repo(repo)
        ensure_repo_intelligence(repo)

        import quor.pipeline.repo_profile.intel as intel_module

        def _fail(*_a, **_kw):
            raise AssertionError("must not re-parse on a true cache hit")

        # intel.py binds these names via `from ... import ...` at import time,
        # so the patch must target intel's own namespace, not symbols.py/
        # graph.py's — patching the source module's attribute would not
        # affect intel.py's already-bound reference to the original function.
        monkeypatch.setattr(intel_module, "extract_file_symbols", _fail)
        monkeypatch.setattr(intel_module, "extract_file_facts", _fail)

        result = ensure_repo_intelligence(repo)
        assert result.action == "cache_hit"

    def test_cache_hit_returns_identical_artifacts_to_the_original_build(self, repo: Path) -> None:
        _make_repo(repo)
        first = ensure_repo_intelligence(repo)
        second = ensure_repo_intelligence(repo)

        assert first.profile == second.profile
        assert first.symbols == second.symbols
        assert first.graph == second.graph


class TestFileModified:
    def test_incremental_rebuild_updates_only_the_changed_file(self, repo: Path) -> None:
        _make_repo(repo)
        ensure_repo_intelligence(repo)

        (repo / "a.py").write_text("def foo():\n    return 42\n\n\ndef extra():\n    pass\n", encoding="utf-8")

        result = ensure_repo_intelligence(repo)

        assert result.action == "incremental"
        assert result.diff is not None
        assert result.diff.modified == ["a.py"]
        a_symbols = next(f for f in result.symbols.files if f.path == "a.py")
        assert {s.name for s in a_symbols.symbols} == {"foo", "extra"}

    def test_unaffected_file_is_not_reparsed(self, repo: Path, monkeypatch) -> None:
        _make_repo(repo)
        ensure_repo_intelligence(repo)
        (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")

        import quor.pipeline.repo_profile.intel as intel_module
        from quor.pipeline.repo_profile.symbols import extract_file_symbols as original

        seen_paths: list[str] = []

        def _tracking(root, rel_path, language):
            seen_paths.append(rel_path)
            return original(root, rel_path, language)

        monkeypatch.setattr(intel_module, "extract_file_symbols", _tracking)

        ensure_repo_intelligence(repo)

        assert seen_paths == ["a.py"]  # b.py was never touched, never re-parsed


class TestFileDeleted:
    def test_deleted_file_removed_from_symbols_and_graph(self, repo: Path) -> None:
        _make_repo(repo)
        ensure_repo_intelligence(repo)

        (repo / "b.py").unlink()
        _git_add_all(repo)

        result = ensure_repo_intelligence(repo)

        assert result.action == "incremental"
        assert result.diff is not None
        assert result.diff.deleted == ["b.py"]
        assert {f.path for f in result.symbols.files} == {"a.py"}
        assert all(edge.source_file != "b.py" for edge in result.graph.edges)


class TestFileRenamed:
    def test_pure_rename_is_not_reparsed(self, repo: Path, monkeypatch) -> None:
        _make_repo(repo)
        ensure_repo_intelligence(repo)

        (repo / "a.py").rename(repo / "renamed.py")
        _git_add_all(repo)

        import quor.pipeline.repo_profile.intel as intel_module

        def _fail(*_a, **_kw):
            raise AssertionError("a pure rename must not require re-parsing")

        monkeypatch.setattr(intel_module, "extract_file_symbols", _fail)

        result = ensure_repo_intelligence(repo)

        assert result.action == "incremental"
        assert result.diff is not None
        assert result.diff.renamed == [("a.py", "renamed.py")]
        assert {f.path for f in result.symbols.files} == {"renamed.py", "b.py"}


class TestVersionChange:
    def test_version_change_forces_a_full_rebuild(self, repo: Path, monkeypatch) -> None:
        _make_repo(repo)
        ensure_repo_intelligence(repo)

        monkeypatch.setattr(quor, "__version__", "999.0.0")
        messages: list[str] = []

        result = ensure_repo_intelligence(repo, echo=messages.append)

        assert result.action == "version_rebuild"
        assert any("version changed" in m for m in messages)

        state = intel_store.load_state(repo)
        assert state is not None
        assert state.quor_version == "999.0.0"


class TestCorruptedCache:
    def test_corrupted_state_file_forces_full_rebuild(self, repo: Path) -> None:
        _make_repo(repo)
        ensure_repo_intelligence(repo)

        (intel_store.cache_dir(repo) / "state.json").write_bytes(b"{not valid")

        result = ensure_repo_intelligence(repo)
        assert result.action == "corrupted_rebuild"

    def test_corrupted_sibling_artifact_forces_full_rebuild(self, repo: Path) -> None:
        _make_repo(repo)
        ensure_repo_intelligence(repo)

        # state.json is fine, but a sibling artifact is unreadable.
        (intel_store.cache_dir(repo) / "graph_facts.json").write_bytes(b"{not valid")

        result = ensure_repo_intelligence(repo)
        assert result.action == "corrupted_rebuild"


class TestDeterminism:
    def test_repeated_runs_on_an_unchanged_repo_are_byte_identical(self, repo: Path) -> None:
        _make_repo(repo)
        first = ensure_repo_intelligence(repo)
        second = ensure_repo_intelligence(repo)
        third = ensure_repo_intelligence(repo)

        assert first.profile == second.profile == third.profile
        assert first.symbols == second.symbols == third.symbols
        assert first.graph == second.graph == third.graph

    def test_full_rebuild_and_incremental_reach_the_same_result(self, repo: Path) -> None:
        _make_repo(repo)
        ensure_repo_intelligence(repo)
        (repo / "a.py").write_text("def foo():\n    return 99\n", encoding="utf-8")

        incremental = ensure_repo_intelligence(repo)
        forced = ensure_repo_intelligence(repo, rebuild=True)

        assert incremental.symbols == forced.symbols
        assert incremental.graph == forced.graph
        assert incremental.profile == forced.profile


class TestRebuildFlag:
    def test_rebuild_flag_bypasses_a_true_cache_hit(self, repo: Path) -> None:
        _make_repo(repo)
        ensure_repo_intelligence(repo)

        result = ensure_repo_intelligence(repo, rebuild=True)

        assert result.action == "forced_rebuild"
        assert result.diff is None


class TestFileIntelligence:
    """QB-079: `file_intelligence.json` — built alongside the other four
    cache files, covering every walked file (not just AST-covered ones,
    per `intel.py::_build_file_intelligence()`'s own docstring)."""

    def test_full_rebuild_covers_every_walked_file(self, repo: Path) -> None:
        _make_repo(repo)
        (repo / "README.md").write_text("# hi\n", encoding="utf-8")
        ensure_repo_intelligence(repo)

        entries = intel_store.load_file_intelligence(repo)
        assert entries is not None
        assert set(entries) == {"a.py", "b.py", "README.md"}
        assert entries["a.py"].kind == "source"
        assert entries["a.py"].language == "python"
        assert entries["b.py"].imports == 1  # b.py imports a.py
        assert entries["a.py"].imported_by == 1

    def test_incremental_refresh_updates_the_changed_file(self, repo: Path) -> None:
        _make_repo(repo)
        ensure_repo_intelligence(repo)

        (repo / "a.py").write_text("def foo():\n    pass\n\n\ndef extra():\n    pass\n", encoding="utf-8")
        result = ensure_repo_intelligence(repo)
        assert result.action == "incremental"

        entries = intel_store.load_file_intelligence(repo)
        assert entries is not None
        assert entries["a.py"].top_symbols == ["foo", "extra"]

    def test_cache_hit_with_valid_file_intelligence_leaves_it_untouched(self, repo: Path) -> None:
        _make_repo(repo)
        ensure_repo_intelligence(repo)
        path = intel_store.cache_dir(repo) / "file_intelligence.json"
        before = path.read_bytes()

        result = ensure_repo_intelligence(repo)

        assert result.action == "cache_hit"
        assert path.read_bytes() == before

    def test_cache_hit_backfills_a_pre_qb079_cache(self, repo: Path) -> None:
        """A cache built before QB-079 has no `file_intelligence.json` at
        all — the very next touch (even a plain cache-hit) must backfill
        it without rewriting the other four files."""
        _make_repo(repo)
        ensure_repo_intelligence(repo)
        cache_dir = intel_store.cache_dir(repo)
        (cache_dir / "file_intelligence.json").unlink()
        # profile.json/symbol_facts.json/graph_facts.json are never rewritten
        # on a true cache-hit; state.json's last_scan_timestamp always
        # advances (a live clock read), so only last_completed_build (which
        # does NOT advance on a cache-hit) is the meaningful invariant there.
        profile_before = (cache_dir / "profile.json").read_bytes()
        symbols_before = (cache_dir / "symbol_facts.json").read_bytes()
        graph_before = (cache_dir / "graph_facts.json").read_bytes()
        last_completed_before = intel_store.load_state(repo).last_completed_build  # type: ignore[union-attr]

        result = ensure_repo_intelligence(repo)

        assert result.action == "cache_hit"
        assert intel_store.load_file_intelligence(repo) is not None
        assert (cache_dir / "profile.json").read_bytes() == profile_before
        assert (cache_dir / "symbol_facts.json").read_bytes() == symbols_before
        assert (cache_dir / "graph_facts.json").read_bytes() == graph_before
        state_after = intel_store.load_state(repo)
        assert state_after is not None
        assert state_after.last_completed_build == last_completed_before

    def test_cache_hit_backfills_a_stale_version_file_intelligence(self, repo: Path) -> None:
        _make_repo(repo)
        ensure_repo_intelligence(repo)
        path = intel_store.cache_dir(repo) / "file_intelligence.json"

        data = orjson.loads(path.read_bytes())
        data["version"] = data["version"] + 1
        path.write_bytes(orjson.dumps(data))

        result = ensure_repo_intelligence(repo)

        assert result.action == "cache_hit"
        assert intel_store.load_file_intelligence(repo) is not None


class TestPerformanceRegression:
    def test_large_unchanged_repo_reparses_zero_files_on_cache_hit(self, repo: Path, monkeypatch) -> None:
        _init_git_repo(repo)
        for i in range(50):
            (repo / f"mod_{i}.py").write_text(f"def f_{i}():\n    pass\n", encoding="utf-8")
        _git_add_all(repo)

        ensure_repo_intelligence(repo)

        import quor.pipeline.repo_profile.intel as intel_module

        symbol_calls = []
        graph_calls = []
        monkeypatch.setattr(
            intel_module, "extract_file_symbols", lambda *a, **kw: symbol_calls.append(a) or (None, None)
        )
        monkeypatch.setattr(
            intel_module, "extract_file_facts", lambda *a, **kw: graph_calls.append(a) or (None, None)
        )

        result = ensure_repo_intelligence(repo)

        assert result.action == "cache_hit"
        assert symbol_calls == []
        assert graph_calls == []

    def test_one_changed_file_among_many_reparses_exactly_one(self, repo: Path, monkeypatch) -> None:
        _init_git_repo(repo)
        for i in range(50):
            (repo / f"mod_{i}.py").write_text(f"def f_{i}():\n    pass\n", encoding="utf-8")
        _git_add_all(repo)

        ensure_repo_intelligence(repo)
        (repo / "mod_7.py").write_text("def f_7():\n    return 1\n", encoding="utf-8")

        import quor.pipeline.repo_profile.intel as intel_module
        from quor.pipeline.repo_profile.symbols import extract_file_symbols as original

        seen_paths: list[str] = []

        def _tracking(root, rel_path, language):
            seen_paths.append(rel_path)
            return original(root, rel_path, language)

        monkeypatch.setattr(intel_module, "extract_file_symbols", _tracking)

        result = ensure_repo_intelligence(repo)

        assert result.action == "incremental"
        assert seen_paths == ["mod_7.py"]
