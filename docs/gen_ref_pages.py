"""Generate API reference pages for mkdocs."""

from __future__ import annotations

from pathlib import Path

import mkdocs_gen_files

PACKAGE_ROOT = Path("gutenbit")
nav = mkdocs_gen_files.Nav()
nav[("Overview",)] = "index.md"

for source_path in sorted(PACKAGE_ROOT.rglob("*.py")):
    module_path = source_path.with_suffix("")
    doc_path = source_path.relative_to(PACKAGE_ROOT.parent).with_suffix(".md")
    full_doc_path = Path("reference", doc_path)
    parts = list(module_path.parts)

    if parts[-1] == "__main__":
        continue
    if parts[-1] == "__init__":
        parts = parts[:-1]
        doc_path = doc_path.with_name("index.md")
        full_doc_path = full_doc_path.with_name("index.md")

    ident = ".".join(parts)
    nav[parts] = doc_path.as_posix()

    with mkdocs_gen_files.open(full_doc_path, "w") as fd:
        fd.write(f"# `{ident}`\n\n")
        fd.write(f"::: {ident}\n")

    mkdocs_gen_files.set_edit_path(full_doc_path, source_path)

with mkdocs_gen_files.open("reference/SUMMARY.md", "w") as nav_file:
    nav_file.writelines(nav.build_literate_nav())
