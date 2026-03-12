"""Runtime versioning regression tests."""

from __future__ import annotations

import gutenbit


def test_package_exports_string_version():
    assert isinstance(gutenbit.__version__, str)
    assert gutenbit.__version__
