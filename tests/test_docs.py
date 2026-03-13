"""Documentation regression tests."""

from __future__ import annotations

import importlib.util
import re
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest
from mkdocs.structure.files import Files

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


def _load_docs_hooks():
    spec = importlib.util.spec_from_file_location("docs_hooks", REPO_ROOT / "docs" / "hooks.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_readme_branding_and_cli_wording():
    readme = (REPO_ROOT / "README.md").read_text()
    assert readme.startswith(
        '<img src="assets/brand/gutenbit-brand.png" alt="gutenbit brand mark" width="220">'
    )
    assert (
        "gutenbit is a command line tool for fast local search across public-domain "
        "literary works." in readme
    )
    assert "## CLI Install" in readme
    assert "## CLI Example" in readme
    assert "gutenbit can also be used as a python module. Add it to your project with:" in readme
    assert "uv add gutenbit" in readme
    assert "# gutenbit" not in readme


def test_docs_home_branding_and_cli_wording():
    home = (REPO_ROOT / "docs" / "index.md").read_text()
    assert '<h1 class="identity-wordmark">' in home
    assert '<span class="identity-wordmark__sr-only">gutenbit</span>' in home
    assert "A command line tool for fast local search across public-domain literary works." in home
    assert "## CLI Install" in home
    assert "## CLI Example" in home
    assert "gutenbit can also be used as a python module. Add it to your project with:" in home
    assert "uv add gutenbit" in home
    assert "assets/brand/gutenbit-brand.png" not in home


def test_mkdocs_nav_orders_cli_before_python_api():
    mkdocs = (REPO_ROOT / "mkdocs.yml").read_text()
    assert mkdocs.index("- CLI: cli.md") < mkdocs.index("- Python API: python-api.md")


def test_docs_theme_uses_shared_logo_asset():
    mkdocs = (REPO_ROOT / "mkdocs.yml").read_text()
    assert "logo: assets/brand/gb-star-logo.png" in mkdocs
    assert (REPO_ROOT / "assets" / "brand" / "gb-star-logo.png").exists()
    assert (REPO_ROOT / "assets" / "brand" / "gutenbit-brand.png").exists()
    assert not (REPO_ROOT / "docs" / "assets" / "images" / "gb-star-logo.png").exists()


def test_docs_hooks_publish_shared_logo_asset():
    docs_hooks = _load_docs_hooks()
    files = Files([])
    config = SimpleNamespace(
        site_dir=str(REPO_ROOT / "site"),
        use_directory_urls=True,
        plugins=SimpleNamespace(_current_plugin=None),
    )

    docs_hooks.on_files(files, config)

    logo = files.get_file_from_path("assets/brand/gb-star-logo.png")

    assert logo is not None
    assert logo.abs_src_path == str(REPO_ROOT / "assets" / "brand" / "gb-star-logo.png")
    assert files.get_file_from_path("assets/brand/gutenbit-brand.png") is None


def test_docs_header_uses_theme_logo_without_custom_overrides():
    assert not (REPO_ROOT / "docs" / "overrides" / "partials" / "header.html").exists()
    assert not (REPO_ROOT / "docs" / "overrides" / "partials" / "site-brand.html").exists()


def test_docs_brand_css_uses_theme_logo_and_homepage_wordmark():
    extra_css = (REPO_ROOT / "docs" / "stylesheets" / "extra.css").read_text()
    assert ".md-header__button.md-logo img" in extra_css
    assert ".md-header__title .md-ellipsis" in extra_css
    assert ".homepage-identity .identity-wordmark" in extra_css
    assert ".homepage-brand-mark" not in extra_css


@pytest.mark.parametrize("command", DOCUMENTED_COMMANDS)
def test_documented_cli_commands_parse(command: str):
    parser = _build_parser()
    namespace = parser.parse_args(shlex.split(command.removeprefix("gutenbit ")))
    assert namespace.command is not None
