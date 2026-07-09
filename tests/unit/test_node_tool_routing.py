"""QB-006B/QB-006C: tool-aware routing through npm/npx/pnpm/yarn wrappers.

Routing is pure command-string matching in FilterRegistry — no new stage,
no classifier change, no package.json inspection, no content-based
decisions. These tests cover:
  - successful routing: a wrapped, literally-named tool (eslint, tsc, jest,
    vitest, prettier) resolves to its own filter regardless of which
    wrapper/shape invoked it. `next lint` also routes to the eslint filter
    (QB-006C), since it runs ESLint under the hood.
  - fallback routing: a wrapped tool with no specific filter of its own
    falls through to the generic npm/npx/pnpm/yarn filter — an emergent
    property of "first match wins", not special-cased code.
  - exclusions: `<wrapper> run <script>` never routes, even when the
    script happens to be named after a known tool — resolving that would
    require reading package.json, which is out of scope by requirement.
  - interaction with the classifier: transparent prefixes (sudo, docker
    exec, env vars), pipe safety, and structured-output exclusion are all
    decided by quor/rewrite/ before FilterRegistry ever sees the command,
    and this routing is a filter-layer concern only — these are regression
    checks proving that boundary is intact.
  - boundary cases: word-boundary precision (eslint-plugin-foo must not
    match), case sensitivity, flags before the tool name.
"""

from __future__ import annotations

from quor.filters.registry import FilterRegistry
from quor.rewrite.classifier import classify_command
from quor.rewrite.invocation import get_quor_invocation

Q = get_quor_invocation()


def _builtin_only() -> FilterRegistry:
    return FilterRegistry(skip_user=True, skip_project=True)


def _matched_filter_name(command: str) -> str | None:
    registry = _builtin_only()
    fc = registry.find(command)
    return fc.name if fc else None


# ---------------------------------------------------------------------------
# Successful routing: eslint, across every documented wrapper shape
# ---------------------------------------------------------------------------


class TestSuccessfulRouting:
    def test_npx_eslint(self) -> None:
        assert _matched_filter_name("npx eslint") == "eslint"

    def test_npx_eslint_with_args(self) -> None:
        assert _matched_filter_name("npx eslint .") == "eslint"

    def test_npx_eslint_with_leading_flag(self) -> None:
        assert _matched_filter_name("npx -y eslint .") == "eslint"

    def test_npm_exec_eslint(self) -> None:
        assert _matched_filter_name("npm exec eslint") == "eslint"

    def test_npm_exec_double_dash_eslint(self) -> None:
        assert _matched_filter_name("npm exec -- eslint .") == "eslint"

    def test_pnpm_exec_eslint(self) -> None:
        assert _matched_filter_name("pnpm exec eslint") == "eslint"

    def test_pnpm_dlx_eslint(self) -> None:
        assert _matched_filter_name("pnpm dlx eslint") == "eslint"

    def test_yarn_exec_eslint(self) -> None:
        assert _matched_filter_name("yarn exec eslint") == "eslint"

    def test_yarn_bare_eslint_shorthand(self) -> None:
        """Yarn classic's implicit `yarn <binary>` shorthand for a locally
        installed package binary — must route, unlike yarn's own subcommands."""
        assert _matched_filter_name("yarn eslint") == "eslint"

    def test_yarn_bare_eslint_with_args(self) -> None:
        assert _matched_filter_name("yarn eslint src/ --fix") == "eslint"

    def test_next_lint_routes_to_eslint(self) -> None:
        """QB-006C: `next lint` runs ESLint under the hood and produces
        identical output — reuses the eslint filter rather than a new one."""
        assert _matched_filter_name("next lint") == "eslint"

    def test_next_lint_with_args_routes_to_eslint(self) -> None:
        assert _matched_filter_name("next lint --dir src") == "eslint"


# ---------------------------------------------------------------------------
# Successful routing: tsc, jest, vitest, prettier (QB-006C) — each has its
# own dedicated filter, reachable bare or through any documented wrapper.
# ---------------------------------------------------------------------------


class TestQB006CToolRouting:
    def test_bare_tsc(self) -> None:
        assert _matched_filter_name("tsc --noEmit") == "tsc"

    def test_npx_tsc(self) -> None:
        assert _matched_filter_name("npx tsc") == "tsc"

    def test_pnpm_exec_tsc(self) -> None:
        assert _matched_filter_name("pnpm exec tsc") == "tsc"

    def test_pnpm_dlx_tsc(self) -> None:
        assert _matched_filter_name("pnpm dlx tsc") == "tsc"

    def test_yarn_bare_tsc(self) -> None:
        assert _matched_filter_name("yarn tsc") == "tsc"

    def test_bare_jest(self) -> None:
        assert _matched_filter_name("jest") == "jest"

    def test_npx_jest(self) -> None:
        assert _matched_filter_name("npx jest") == "jest"

    def test_npm_exec_jest(self) -> None:
        assert _matched_filter_name("npm exec jest") == "jest"

    def test_pnpm_exec_jest(self) -> None:
        assert _matched_filter_name("pnpm exec jest") == "jest"

    def test_yarn_bare_jest(self) -> None:
        assert _matched_filter_name("yarn jest") == "jest"

    def test_bare_vitest(self) -> None:
        assert _matched_filter_name("vitest run") == "vitest"

    def test_npx_vitest(self) -> None:
        assert _matched_filter_name("npx vitest") == "vitest"

    def test_yarn_bare_vitest(self) -> None:
        assert _matched_filter_name("yarn vitest") == "vitest"

    def test_bare_prettier(self) -> None:
        assert _matched_filter_name("prettier --check .") == "prettier"

    def test_npx_prettier(self) -> None:
        assert _matched_filter_name("npx prettier .") == "prettier"

    def test_yarn_bare_prettier(self) -> None:
        assert _matched_filter_name("yarn prettier --write .") == "prettier"

    def test_bare_next(self) -> None:
        assert _matched_filter_name("next build") == "next"

    def test_bare_turbo(self) -> None:
        assert _matched_filter_name("turbo run build") == "turbo"


# ---------------------------------------------------------------------------
# Fallback routing: no specific filter exists for a random wrapped tool
# ---------------------------------------------------------------------------


class TestFallbackRouting:
    def test_unrecognized_wrapped_tool_falls_back(self) -> None:
        assert _matched_filter_name("npx some-random-cli-tool") == "npx"


# ---------------------------------------------------------------------------
# Exclusions: package.json-mediated scripts never route, by requirement
# ---------------------------------------------------------------------------


class TestScriptInvocationsNeverRoute:
    def test_npm_test_stays_generic(self) -> None:
        assert _matched_filter_name("npm test") == "npm"

    def test_npm_run_build_stays_generic(self) -> None:
        assert _matched_filter_name("npm run build") == "npm"

    def test_npm_run_lint_stays_generic(self) -> None:
        assert _matched_filter_name("npm run lint") == "npm"

    def test_npm_run_script_literally_named_eslint_still_excluded(self) -> None:
        """The script *name* happens to be 'eslint', but `npm run` always
        goes through package.json — resolving what it really runs would
        require reading package.json, which is out of scope. Must stay generic."""
        assert _matched_filter_name("npm run eslint") == "npm"

    def test_yarn_run_eslint_still_excluded(self) -> None:
        assert _matched_filter_name("yarn run eslint") == "yarn"

    def test_pnpm_run_eslint_still_excluded(self) -> None:
        assert _matched_filter_name("pnpm run eslint") == "pnpm"


# ---------------------------------------------------------------------------
# Regression: classifier boundary (transparent prefixes, pipes, structured
# output) is untouched by QB-006B — these all resolve before FilterRegistry
# ever sees the command, and quor/rewrite/ was not modified for this item.
# ---------------------------------------------------------------------------


class TestClassifierBoundaryUnaffected:
    def test_sudo_npx_eslint_rewrites_and_preserves_wrapped_command(self) -> None:
        r = classify_command("sudo npx eslint .")
        assert r.should_rewrite is True
        assert r.rewritten == f"sudo {Q} npx eslint ."
        # The portion FilterRegistry will see is exactly "npx eslint .".
        assert _matched_filter_name("npx eslint .") == "eslint"

    def test_docker_exec_npx_eslint_rewrites_and_preserves_wrapped_command(self) -> None:
        r = classify_command("docker exec mycontainer npx eslint .")
        assert r.should_rewrite is True
        assert r.rewritten == f"docker exec mycontainer {Q} npx eslint ."

    def test_env_prefix_npx_eslint_rewrites_and_preserves_wrapped_command(self) -> None:
        r = classify_command("CI=true npx eslint .")
        assert r.should_rewrite is True
        assert r.rewritten == f"CI=true {Q} npx eslint ."

    def test_pipe_to_safe_target_rewrites_first_segment_only(self) -> None:
        r = classify_command("npx eslint . | grep error")
        assert r.should_rewrite is True
        assert r.rewritten == f"{Q} npx eslint . | grep error"

    def test_pipe_to_unsafe_target_excluded(self) -> None:
        r = classify_command("npx eslint . | xargs echo")
        assert r.should_rewrite is False
        assert r.rewritten is None

    def test_structured_output_flag_excludes_rewrite(self) -> None:
        r = classify_command("npx eslint --format=json .")
        assert r.should_rewrite is False
        assert r.rewritten is None

    def test_bunx_still_transparent_and_unrelated_to_routing(self) -> None:
        """bunx stays out of scope entirely (QB-006A decision) — confirms
        QB-006B didn't accidentally widen Node-tool routing to Bun."""
        r = classify_command("bunx eslint .")
        assert r.should_rewrite is False


# ---------------------------------------------------------------------------
# Boundary cases
# ---------------------------------------------------------------------------


class TestRoutingBoundaryCases:
    def test_eslint_plugin_package_name_does_not_match(self) -> None:
        """Word-boundary precision: a package name that merely starts with
        'eslint' (a real, common npm naming convention) must not misroute."""
        assert _matched_filter_name("npx eslint-plugin-foo") == "npx"

    def test_eslint_config_package_name_does_not_match(self) -> None:
        assert _matched_filter_name("npx eslintconfig-check") == "npx"

    def test_yarn_eslintcache_binary_does_not_match(self) -> None:
        assert _matched_filter_name("yarn eslintcache") == "yarn"

    def test_yarn_own_subcommands_are_not_shadowed(self) -> None:
        """Regression: the bare-yarn-shorthand pattern must never capture
        yarn's own first-class subcommands."""
        for cmd in ("yarn add lodash", "yarn install", "yarn upgrade", "yarn remove lodash"):
            assert _matched_filter_name(cmd) == "yarn", cmd

    def test_multiple_flags_before_tool_name(self) -> None:
        assert _matched_filter_name("npx -y --no-install eslint .") == "eslint"

    def test_case_sensitivity_uppercase_tool_name_does_not_match(self) -> None:
        """Real npm/npx/yarn package names are case-sensitive and lowercase
        by convention; an uppercase invocation is not a real eslint call."""
        assert _matched_filter_name("npx ESLint") == "npx"


# ---------------------------------------------------------------------------
# QB-006C boundary cases: bare-command word-boundary precision.
#
# A plain `\b` word-boundary regex (e.g. `^tsc\b`) would incorrectly match
# real, unrelated binaries like `tsc-watch` or `jest-environment-jsdom`,
# because `\b` fires on the transition between a word character ("c") and a
# non-word character ("-"). Every QB-006C bare-command pattern uses
# `(?=\s|$)` instead, exactly like the wrapped-form patterns already do —
# these tests are the regression check for that choice.
# ---------------------------------------------------------------------------


class TestQB006CBoundaryCases:
    def test_tsc_watch_binary_does_not_match_bare_tsc(self) -> None:
        assert _matched_filter_name("tsc-watch") != "tsc"

    def test_npx_tsc_watch_does_not_match_tsc(self) -> None:
        assert _matched_filter_name("npx tsc-watch") == "npx"

    def test_jest_environment_jsdom_binary_does_not_match_bare_jest(self) -> None:
        assert _matched_filter_name("jest-environment-jsdom") != "jest"

    def test_vitest_config_check_binary_does_not_match_bare_vitest(self) -> None:
        assert _matched_filter_name("vitest-config-check") != "vitest"

    def test_prettier_plugin_binary_does_not_match_bare_prettier(self) -> None:
        assert _matched_filter_name("prettier-plugin-foo --version") != "prettier"

    def test_nextjs_lookalike_binary_does_not_match_bare_next(self) -> None:
        assert _matched_filter_name("nextjs-bundle-analyzer") != "next"

    def test_turbo_lookalike_binary_does_not_match_bare_turbo(self) -> None:
        assert _matched_filter_name("turbo-something run") != "turbo"

    def test_next_lint_config_does_not_falsely_match_next_lint_routing(self) -> None:
        """`next lint-config` (hypothetical subcommand) must not be
        misrouted to the eslint filter via a loose `lint\\b` boundary."""
        assert _matched_filter_name("next lint-config") != "eslint"
