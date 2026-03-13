"""Documentation regression tests."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from gutenbit.cli import _build_parser

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_PATHS = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "index.md",
    REPO_ROOT / "docs" / "getting-started.md",
    REPO_ROOT / "docs" / "cli.md",
)
_INLINE_COMMENT_RE = re.compile(r"\s+#.*$")


def _extract_cli_commands(path: Path) -> list[str]:
    commands: list[str] = []
    in_bash_block = False
    for raw_line in path.read_text().splitlines():
        stripped = raw_line.strip()
        if stripped == "```bash":
            in_bash_block = True
            continue
        if stripped.startswith("```") and in_bash_block:
            in_bash_block = False
            continue
        if not in_bash_block:
            continue
        if not stripped.startswith("gutenbit "):
            continue
        commands.append(_INLINE_COMMENT_RE.sub("", stripped).rstrip())
    return commands


DOCUMENTED_COMMANDS = [
    pytest.param(command, id=command)
    for path in DOC_PATHS
    for command in _extract_cli_commands(path)
]


@pytest.mark.parametrize(
    "path",
    ["README.md", "docs/index.md", "docs/getting-started.md", "docs/cli.md"],
)
def test_install_docs_omit_update_shell(path: str):
    assert "uv tool update-shell" not in (REPO_ROOT / path).read_text()


def test_readme_orders_cli_then_python_then_documentation():
    readme = (REPO_ROOT / "README.md").read_text()
    assert readme.index("## CLI") < readme.index("## Python")
    assert readme.index("## Python") < readme.index("## Documentation")
    assert readme.index("## Documentation") < readme.index("## Project Gutenberg Access")


def test_readme_documentation_links_order_cli_before_python_api():
    readme = (REPO_ROOT / "README.md").read_text()
    assert readme.index("[CLI](docs/cli.md)") < readme.index("[Python API](docs/python-api.md)")


def test_docs_home_next_steps_order_cli_before_python_api():
    home = (REPO_ROOT / "docs" / "index.md").read_text()
    assert home.index("- [CLI](cli.md)") < home.index("- [Python API](python-api.md)")


def test_mkdocs_nav_orders_cli_before_python_api():
    mkdocs = (REPO_ROOT / "mkdocs.yml").read_text()
    assert mkdocs.index("- CLI: cli.md") < mkdocs.index("- Python API: python-api.md")


def test_docs_theme_uses_committed_logo_asset():
    mkdocs = (REPO_ROOT / "mkdocs.yml").read_text()
    assert "logo: assets/images/gb-star-logo.png" in mkdocs
    assert (REPO_ROOT / "docs" / "assets" / "images" / "gb-star-logo.png").exists()


def test_docs_header_uses_theme_logo_without_custom_overrides():
    assert not (REPO_ROOT / "docs" / "overrides" / "partials" / "header.html").exists()
    assert not (REPO_ROOT / "docs" / "overrides" / "partials" / "site-brand.html").exists()


def test_docs_header_brand_css_uses_theme_logo_and_hides_header_text():
    extra_css = (REPO_ROOT / "docs" / "stylesheets" / "extra.css").read_text()
    assert ".md-header__button.md-logo img" in extra_css
    assert ".md-header__title .md-ellipsis" in extra_css
    assert ".md-header__button.md-logo .identity-wordmark" not in extra_css


@pytest.mark.parametrize("command", DOCUMENTED_COMMANDS)
def test_documented_cli_commands_parse(command: str):
    parser = _build_parser()
    namespace = parser.parse_args(shlex.split(command.removeprefix("gutenbit ")))
    assert namespace.command is not None
