"""Documentation regression tests."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import click
import pytest

from gutenbit.cli import _cli

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_PATHS = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "index.md",
    REPO_ROOT / "docs" / "getting-started.md",
    REPO_ROOT / "docs" / "cli.md",
)
_INLINE_COMMENT_RE = re.compile(r"\s+#.*$")


def _css_block(css: str, selector: str) -> str:
    start = css.index(selector)
    open_brace = css.index("{", start)
    depth = 0
    for index in range(open_brace, len(css)):
        char = css[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return css[open_brace + 1 : index]
    raise ValueError(f"Unclosed CSS block for {selector!r}")


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


@pytest.mark.parametrize("command", DOCUMENTED_COMMANDS)
def test_documented_cli_commands_parse(command: str):
    args = shlex.split(command.removeprefix("gutenbit "))
    try:
        ctx = _cli.make_context(
            "gutenbit", list(args), allow_extra_args=True, allow_interspersed_args=False
        )
    except click.exceptions.UsageError as exc:
        pytest.fail(f"Command failed to parse: {command!r} — {exc}")
    # In Click 8.x, remaining tokens (subcommand + its args) are in protected_args;
    # Click 9.0 merges them into args. Support both.
    cmd_args = ctx.protected_args if ctx.protected_args else ctx.args
    assert cmd_args, f"No subcommand found in: {command!r}"
    cmd_name = cmd_args[0]
    assert cmd_name in _cli.commands, f"Unknown subcommand {cmd_name!r} in: {command!r}"


def test_mobile_header_hides_logo_and_restores_title():
    css = (REPO_ROOT / "docs" / "stylesheets" / "extra.css").read_text()
    mobile_header_block = _css_block(css, "@media screen and (max-width: 76.234375em)")

    assert ".md-header__button.md-logo {\n    display: none;\n  }" in mobile_header_block
    assert ".md-header__title .md-ellipsis {\n    visibility: visible;\n  }" in mobile_header_block
