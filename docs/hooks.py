"""MkDocs hooks for docs-site configuration."""

from __future__ import annotations

from importlib.metadata import version
from typing import Any


def on_config(config: Any, **_: Any) -> Any:
    """Inject the package version into the repository label."""
    package_version = version("gutenbit")
    base_name = config.get("repo_name") or "Gutenbit"
    config["repo_name"] = f"{base_name} v{package_version}"
    return config
