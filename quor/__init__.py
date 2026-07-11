from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("quor")
except PackageNotFoundError:
    # Editable/uninstalled case: running from a source checkout with no
    # installed distribution for importlib.metadata to find (e.g. invoked via
    # `python -m quor` from a checkout that was never `pip install`'d at all).
    # Kept in sync with pyproject.toml's [project].version by
    # test_version_matches_pyproject (QB-020) the same way __version__ itself
    # used to be.
    __version__ = "0.4.1"
