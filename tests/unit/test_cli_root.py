"""CLI tests for QB-073's root-level polish: `quor version`, `--version`,
and `--help`'s command grouping."""

from __future__ import annotations

from typer.testing import CliRunner

from quor import __version__
from quor.cli.main import app

runner = CliRunner()


class TestVersionCommand:
    def test_version_subcommand_prints_version(self) -> None:
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert result.stdout.strip() == f"quor {__version__}"

    def test_version_flag_prints_version(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert result.stdout.strip() == f"quor {__version__}"

    def test_version_flag_and_subcommand_agree(self) -> None:
        via_flag = runner.invoke(app, ["--version"])
        via_subcommand = runner.invoke(app, ["version"])
        via_bare = runner.invoke(app, [])
        assert via_flag.stdout == via_subcommand.stdout == via_bare.stdout

    def test_reachable_without_dispatcher_fallthrough(self) -> None:
        """Regression guard for the exact real bug ADR-037 first caught for
        `quor map`: `version` must be routed to the CLI, never treated as a
        literal shell command name by the dispatcher (which previously
        failed with a raw WinError 2 / FileNotFoundError for `quor
        version`, since nothing in `_CLI_COMMANDS` recognized it)."""
        import quor.__main__ as main_module

        assert "version" in main_module._CLI_COMMANDS

    def test_version_flag_is_eager_and_ignores_other_args(self) -> None:
        # --version must win even if garbage follows it, the same way
        # `git --version anything` does — is_eager=True is what guarantees this.
        result = runner.invoke(app, ["--version", "nonexistent-subcommand"])
        assert result.exit_code == 0
        assert result.stdout.strip() == f"quor {__version__}"


class TestHelpGrouping:
    def test_help_groups_commands_into_three_panels(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Installation" in result.stdout
        assert "Analysis" in result.stdout
        assert "Utilities" in result.stdout

    def test_installation_panel_contains_doctor_and_uninstall_hooks(self) -> None:
        """QB-104: `init` was removed along with the hook-based integration;
        `uninstall-hooks` (the migration command for pre-QB-104 installs)
        replaced it in the Installation panel."""
        result = runner.invoke(app, ["--help"])
        installation_section = result.stdout.split("Installation")[1].split("Analysis")[0]
        assert "doctor" in installation_section
        assert "uninstall-hooks" in installation_section

    def test_analysis_panel_contains_map_symbols_graph(self) -> None:
        result = runner.invoke(app, ["--help"])
        analysis_section = result.stdout.split("Analysis")[1].split("Utilities")[0]
        assert "map" in analysis_section
        assert "symbols" in analysis_section
        assert "graph" in analysis_section

    def test_utilities_panel_contains_remaining_commands(self) -> None:
        result = runner.invoke(app, ["--help"])
        utilities_section = result.stdout.split("Utilities")[-1]
        for name in ("explain", "validate", "verify", "gain", "version", "schema"):
            assert name in utilities_section

    def test_help_shows_concise_one_line_descriptions(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "Remove a pre-QB-104 hook installation." in result.stdout
        assert "Generate a deterministic repository context profile." in result.stdout


class TestHelpSubcommand:
    """QB-122: `quor help` must render Quor's own help, not fall through to
    the dispatcher (which, unrecognized, hands "help" to the shell as a
    literal command name — on Windows that resolves to CMD's own built-in
    help, not Quor's)."""

    def test_help_subcommand_prints_help(self) -> None:
        result = runner.invoke(app, ["help"])
        assert result.exit_code == 0
        assert "Installation" in result.stdout
        assert "Analysis" in result.stdout
        assert "Utilities" in result.stdout

    def test_help_subcommand_matches_help_flag(self) -> None:
        via_flag = runner.invoke(app, ["--help"])
        via_subcommand = runner.invoke(app, ["help"])
        assert via_flag.stdout == via_subcommand.stdout

    def test_reachable_without_dispatcher_fallthrough(self) -> None:
        """Same regression class `TestVersionCommand`'s equivalent test
        guards against (ADR-037): `help` must be routed to the CLI, never
        treated as a literal shell command name by the dispatcher."""
        import quor.__main__ as main_module

        assert "help" in main_module._CLI_COMMANDS
