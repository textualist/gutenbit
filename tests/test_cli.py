"""CLI-specific regression tests."""

from __future__ import annotations

import contextlib
import io
from importlib.metadata import version as package_version

import pytest

from gutenbit.cli import main as cli_main


def test_version_flag_matches_installed_metadata():
    out = io.StringIO()
    err = io.StringIO()

    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        with pytest.raises(SystemExit) as excinfo:
            cli_main(["--version"])

    assert excinfo.value.code == 0
    assert err.getvalue() == ""
    assert out.getvalue().strip() == f"gutenbit {package_version('gutenbit')}"
