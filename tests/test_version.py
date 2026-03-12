"""Runtime versioning regression tests."""

from __future__ import annotations

from importlib.metadata import version as package_version

import gutenbit


def test_package_exports_string_version():
    assert isinstance(gutenbit.__version__, str)
    assert gutenbit.__version__


def test_package_version_matches_distribution_metadata():
    assert gutenbit.__version__ == package_version("gutenbit")
