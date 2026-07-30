"""Unit tests for quor/pipeline/repo_profile/intel_store.py (QB-072)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import orjson

from quor.pipeline.ast_summarize.relationship_model import Relationship
from quor.pipeline.ast_summarize.symbol_model import Symbol
from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.graph import FileFacts
from quor.pipeline.repo_profile.intel_model import (
    FileFingerprint,
    FileIntelligenceEntry,
    RepoIntelState,
)
from quor.pipeline.repo_profile.model import RepoProfile, RepoStatistics
from quor.pipeline.repo_profile.symbols_model import FileSymbols


def _sample_state(root: Path) -> RepoIntelState:
    return RepoIntelState(
        schema_version=1,
        quor_version="0.4.1",
        repo_root=root.resolve().as_posix(),
        git_head="a" * 40,
        file_count=2,
        last_scan_timestamp="2026-01-01T00:00:00+00:00",
        last_completed_build="2026-01-01T00:00:00+00:00",
        fingerprints={"a.py": FileFingerprint(size=10, mtime_ns=123, content_hash="deadbeef")},
    )


def _sample_profile(root: Path) -> RepoProfile:
    return RepoProfile(
        root=root.as_posix(),
        statistics=RepoStatistics(
            total_files=1, total_directories=0, primary_language="Python", git_commit_count=1
        ),
    )


class TestRepoKeyAndCacheDir:
    def test_repo_key_is_stable_for_the_same_root(self, tmp_path: Path) -> None:
        assert intel_store.repo_key(tmp_path) == intel_store.repo_key(tmp_path)

    def test_repo_key_differs_across_roots(self, tmp_path: Path) -> None:
        other = tmp_path / "other"
        other.mkdir()
        assert intel_store.repo_key(tmp_path) != intel_store.repo_key(other)

    def test_cache_dir_is_keyed_under_repo_intel_subdir(self, tmp_path: Path) -> None:
        cache_dir = intel_store.cache_dir(tmp_path)
        assert cache_dir.parent.name == "repo_intel"


class TestStateRoundtrip:
    def test_state_exists_false_when_missing(self, tmp_path: Path) -> None:
        assert intel_store.state_exists(tmp_path) is False

    def test_load_state_missing_returns_none(self, tmp_path: Path) -> None:
        assert intel_store.load_state(tmp_path) is None

    def test_save_then_load_state_roundtrips_field_for_field(self, tmp_path: Path) -> None:
        state = _sample_state(tmp_path)
        intel_store.save_state(tmp_path, state)

        assert intel_store.state_exists(tmp_path) is True
        loaded = intel_store.load_state(tmp_path)

        assert loaded == state

    def test_load_state_corrupted_returns_none(self, tmp_path: Path) -> None:
        intel_store.save_state(tmp_path, _sample_state(tmp_path))
        path = intel_store.cache_dir(tmp_path) / "state.json"
        path.write_bytes(b"{not valid json")

        assert intel_store.load_state(tmp_path) is None

    def test_load_state_wrong_shape_returns_none(self, tmp_path: Path) -> None:
        path = intel_store.cache_dir(tmp_path)
        path.mkdir(parents=True, exist_ok=True)
        (path / "state.json").write_bytes(b'{"unexpected": "shape"}')

        assert intel_store.load_state(tmp_path) is None


class TestProfileRoundtrip:
    def test_save_then_load_profile_roundtrips(self, tmp_path: Path) -> None:
        profile = _sample_profile(tmp_path)
        intel_store.save_profile(tmp_path, profile)

        loaded = intel_store.load_profile(tmp_path)

        assert loaded == profile

    def test_load_profile_missing_returns_none(self, tmp_path: Path) -> None:
        assert intel_store.load_profile(tmp_path) is None

    def test_load_profile_corrupted_returns_none(self, tmp_path: Path) -> None:
        intel_store.save_profile(tmp_path, _sample_profile(tmp_path))
        path = intel_store.cache_dir(tmp_path) / "profile.json"
        path.write_bytes(b"not json at all")

        assert intel_store.load_profile(tmp_path) is None


class TestSymbolFactsRoundtrip:
    def test_save_then_load_roundtrips(self, tmp_path: Path) -> None:
        files = {
            "a.py": FileSymbols(
                path="a.py",
                language="python",
                symbols=[Symbol(name="foo", kind="function", line=1, is_public=True)],
            )
        }
        intel_store.save_symbol_facts(tmp_path, files, {"b.py"})

        loaded = intel_store.load_symbol_facts(tmp_path)

        assert loaded is not None
        loaded_files, loaded_failures = loaded
        assert loaded_files == files
        assert loaded_failures == {"b.py"}

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        assert intel_store.load_symbol_facts(tmp_path) is None

    def test_load_corrupted_returns_none(self, tmp_path: Path) -> None:
        intel_store.save_symbol_facts(tmp_path, {}, set())
        path = intel_store.cache_dir(tmp_path) / "symbol_facts.json"
        path.write_bytes(b"{broken")

        assert intel_store.load_symbol_facts(tmp_path) is None


class TestGraphFactsRoundtrip:
    def test_save_then_load_roundtrips(self, tmp_path: Path) -> None:
        facts = {
            "a.py": FileFacts(
                language="python",
                symbol_counts=Counter({"foo": 1}),
                relationships=[Relationship(kind="import", source="", target="os", line=1)],
            )
        }
        intel_store.save_graph_facts(tmp_path, facts, {"b.py"})

        loaded = intel_store.load_graph_facts(tmp_path)

        assert loaded is not None
        loaded_facts, loaded_failures = loaded
        assert loaded_facts == facts
        assert loaded_failures == {"b.py"}

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        assert intel_store.load_graph_facts(tmp_path) is None

    def test_load_corrupted_returns_none(self, tmp_path: Path) -> None:
        intel_store.save_graph_facts(tmp_path, {}, set())
        path = intel_store.cache_dir(tmp_path) / "graph_facts.json"
        path.write_bytes(b"{broken")

        assert intel_store.load_graph_facts(tmp_path) is None


class TestFileIntelligenceRoundtrip:
    def test_file_intelligence_exists_false_when_missing(self, tmp_path: Path) -> None:
        assert intel_store.file_intelligence_exists(tmp_path) is False

    def test_save_then_load_roundtrips_field_for_field(self, tmp_path: Path) -> None:
        entries = {
            "a.py": FileIntelligenceEntry(
                language="python",
                kind="source",
                importance="High",
                imports=3,
                imported_by=61,
                entry_point=False,
                top_symbols=["Foo", "bar"],
                size=1234,
                mtime_ns=567890,
            ),
            "tests/test_a.py": FileIntelligenceEntry(language="python", kind="test"),
        }
        intel_store.save_file_intelligence(tmp_path, entries)

        assert intel_store.file_intelligence_exists(tmp_path) is True
        loaded = intel_store.load_file_intelligence(tmp_path)

        assert loaded == entries

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        assert intel_store.load_file_intelligence(tmp_path) is None

    def test_load_corrupted_returns_none(self, tmp_path: Path) -> None:
        intel_store.save_file_intelligence(tmp_path, {})
        path = intel_store.cache_dir(tmp_path) / "file_intelligence.json"
        path.write_bytes(b"{broken")

        assert intel_store.load_file_intelligence(tmp_path) is None

    def test_load_wrong_shape_returns_none(self, tmp_path: Path) -> None:
        path = intel_store.cache_dir(tmp_path)
        path.mkdir(parents=True, exist_ok=True)
        (path / "file_intelligence.json").write_bytes(b'{"unexpected": "shape"}')

        assert intel_store.load_file_intelligence(tmp_path) is None

    def test_load_version_mismatch_returns_none(self, tmp_path: Path) -> None:
        """A `file_intelligence.json` written at an older/newer
        `FILE_INTELLIGENCE_VERSION` is treated exactly like "missing" —
        not a separate corrupted-vs-stale distinction (see
        `load_file_intelligence()`'s own docstring: this lets `intel.py`'s
        existing backfill-on-next-touch logic handle a version bump the
        same way as a first-time build, with no extra branching)."""
        intel_store.save_file_intelligence(tmp_path, {"a.py": FileIntelligenceEntry(language="python", kind="source")})
        path = intel_store.cache_dir(tmp_path) / "file_intelligence.json"

        data = orjson.loads(path.read_bytes())
        data["version"] = data["version"] + 1
        path.write_bytes(orjson.dumps(data))

        assert intel_store.file_intelligence_exists(tmp_path) is True
        assert intel_store.load_file_intelligence(tmp_path) is None
