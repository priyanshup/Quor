"""Exception hierarchy for Quor.

All Quor exceptions inherit from QuorError.
Exit codes are defined in ExitCode and used by CLI commands.

Rules (from PROJECT_BIBLE.md):
- Never use `assert` for validation — use explicit `if/raise`.
- Every `except` clause is specific. Never use bare `except:`.
- Hook-level code has one top-level `except Exception` guard. Nowhere else.
"""

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    GENERAL_ERROR = 1
    CONFIG_ERROR = 2
    HOOK_ERROR = 3
    PLUGIN_ERROR = 4
    DEPENDENCY_MISSING = 5  # Python version check failure


class QuorError(Exception):
    """Base exception for all Quor errors."""

    def __init__(self, message: str, exit_code: ExitCode = ExitCode.GENERAL_ERROR) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class FilterError(QuorError):
    """A filter could not be loaded or applied."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ExitCode.CONFIG_ERROR)


class ConfigError(QuorError):
    """A configuration file is invalid or could not be parsed."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ExitCode.CONFIG_ERROR)


class HookError(QuorError):
    """The hook adapter encountered an unrecoverable error."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ExitCode.HOOK_ERROR)


class CacheError(QuorError):
    """The plugin discovery cache could not be read or written."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ExitCode.GENERAL_ERROR)


class PluginError(QuorError):
    """A plugin failed to load, validate, or apply."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ExitCode.PLUGIN_ERROR)
