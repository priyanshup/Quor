"""QB-040: end-to-end structured-data Read-hook compression.

Mirrors tests/unit/test_read_hook_ast_summarization.py's pattern exactly
(real stdin -> stdout JSON contract via `run_hook`), for the two new Read
routing paths QB-040 adds to `quor/adapters/claude_read.py`:
  - `.json`/`.jsonc`/`.yaml`/`.yml`/`.toml` — by-extension lookup
    (`_STRUCTURED_DATA_FILTER_NAMES_BY_EXTENSION`), same shape as the
    existing source-code path.
  - `.env`/`.ini` — bare-path `FilterRegistry.find()` match plus a
    `_READ_SUPPORTED_FILTER_NAMES` allowlist entry, same shape as
    markdown/document-text.
"""

from __future__ import annotations

import io
import sys
from unittest.mock import patch

import orjson

from quor.adapters.claude_read import run_hook


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
    result: dict = orjson.loads(fake_stdout.buffer.read())
    return result


def _long_json_deps() -> str:
    entries = ",\n".join(f'    {{"name": "{c}", "version": "1.0"}}' for c in "abcdefgh")
    return '{\n  "dependencies": [\n' + entries + "\n  ]\n}"


class TestReadHookJson:
    def test_json_extension_routes_to_cat_json_and_collapses(self) -> None:
        result = _run_hook(_read_payload("package.json", _long_json_deps()))
        output = result["hookSpecificOutput"].get("updatedToolOutput")
        assert output is not None
        assert '"name": "a"' in output
        assert '"name": "d"' not in output
        assert "more items omitted" in output

    def test_jsonc_extension_also_routes(self) -> None:
        result = _run_hook(_read_payload("tsconfig.jsonc", _long_json_deps()))
        output = result["hookSpecificOutput"].get("updatedToolOutput")
        assert output is not None
        assert "more items omitted" in output

    def test_short_json_passthrough(self) -> None:
        result = _run_hook(_read_payload("package.json", '{"name": "quor"}'))
        assert "updatedToolOutput" not in result["hookSpecificOutput"]


class TestReadHookYaml:
    def test_yaml_extension_routes_and_collapses(self) -> None:
        source = "dependencies:\n" + "\n".join(
            f"  - name: {c}\n    version: 1.0" for c in "abcdefgh"
        ) + "\n"
        result = _run_hook(_read_payload("k8s/deployment.yaml", source))
        output = result["hookSpecificOutput"].get("updatedToolOutput")
        assert output is not None
        assert "name: a" in output
        assert "name: d" not in output

    def test_yml_extension_also_routes(self) -> None:
        # Realistic (not single-character) item values -- a short scalar
        # list's placeholder line can legitimately cost more estimated
        # tokens than the few tiny lines it replaces, correctly triggering
        # FilterRegistry.apply()'s own "never make output larger" safeguard
        # (see quor/filters/registry.py) rather than a bug in this filter.
        packages = ["flask", "requests", "pydantic", "typer", "orjson", "rich", "regex"]
        source = "deps:\n" + "\n".join(f"  - {p}" for p in packages) + "\n"
        result = _run_hook(_read_payload("config.yml", source))
        output = result["hookSpecificOutput"].get("updatedToolOutput")
        assert output is not None
        assert "more items omitted" in output


class TestReadHookToml:
    def test_toml_extension_routes_and_collapses(self) -> None:
        blocks = [f'[[package]]\nname = "{n}"\nversion = "1.0"' for n in "abcdefgh"]
        source = "\n\n".join(blocks) + "\n"
        result = _run_hook(_read_payload("poetry.lock", source))
        output = result["hookSpecificOutput"].get("updatedToolOutput")
        assert output is not None
        assert 'name = "a"' in output
        assert 'name = "d"' not in output
        assert "more [[package]] entries omitted" in output

    def test_short_toml_passthrough(self) -> None:
        result = _run_hook(_read_payload("pyproject.toml", '[project]\nname = "quor"\n'))
        assert "updatedToolOutput" not in result["hookSpecificOutput"]


class TestReadHookDotenv:
    def test_dotenv_strips_comments(self) -> None:
        source = "# secrets below\n\nAPI_KEY=abc123\nDEBUG=true\n"
        result = _run_hook(_read_payload(".env", source))
        output = result["hookSpecificOutput"].get("updatedToolOutput")
        assert output is not None
        assert "API_KEY=abc123" in output
        assert "DEBUG=true" in output
        assert "# secrets below" not in output

    def test_dotenv_variant_extension(self) -> None:
        source = "# comment\nPORT=8080\n"
        result = _run_hook(_read_payload(".env.production", source))
        output = result["hookSpecificOutput"].get("updatedToolOutput")
        assert output is not None
        assert "PORT=8080" in output
        assert "# comment" not in output

    def test_no_comments_all_values_survive(self) -> None:
        # A trailing "\n" splits into a final empty line, which the blank-
        # line strip pattern legitimately (and harmlessly) drops -- so this
        # is not byte-identical passthrough, but every real KEY=VALUE line
        # must still be fully present and untouched.
        result = _run_hook(_read_payload(".env", "PORT=8080\nHOST=localhost\n"))
        output = result["hookSpecificOutput"].get("updatedToolOutput")
        rendered = output if output is not None else "PORT=8080\nHOST=localhost\n"
        assert "PORT=8080" in rendered
        assert "HOST=localhost" in rendered


class TestReadHookIni:
    def test_ini_strips_comments(self) -> None:
        source = "; config\n[app]\nname = quor\n"
        result = _run_hook(_read_payload("settings.ini", source))
        output = result["hookSpecificOutput"].get("updatedToolOutput")
        assert output is not None
        assert "[app]" in output
        assert "name = quor" in output
        assert "; config" not in output
