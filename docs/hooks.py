"""MkDocs hooks for docs-site configuration."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from typing import Any

from mkdocs.structure.files import File, Files

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_ASSET_URIS = {
    "assets/brand/gb-star-logo.png": REPO_ROOT / "assets" / "brand" / "gb-star-logo.png",
}


def on_config(config: Any, **_: Any) -> Any:
    """Inject the package version into the repository label."""
    package_version = version("gutenbit")
    base_name = config.get("repo_name") or "gutenbit"
    config["repo_name"] = f"{base_name} v{package_version}"
    return config


def on_files(files: Files, config: Any, **_: Any) -> Files:
    """Publish shared brand assets into the docs site."""
    for asset_uri, asset_path in DOC_ASSET_URIS.items():
        if not asset_path.exists():
            raise FileNotFoundError(f"Missing brand asset: {asset_path}")
        if files.get_file_from_path(asset_uri) is None:
            files.append(File.generated(config, asset_uri, abs_src_path=str(asset_path)))
    return files
