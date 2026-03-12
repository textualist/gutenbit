# Releasing gutenbit

gutenbit uses tag-driven versioning via `hatch-vcs`. Do not edit version strings in source files.

Stable releases are published to PyPI. GitHub Releases receive the same verified wheel and sdist after the PyPI publish succeeds.

## One-time setup

1. Create a protected GitHub Actions environment named `pypi` in the repository settings.
2. Require manual approval on the `pypi` environment before deployments are allowed.
3. On PyPI, create the `gutenbit` project or a pending publisher entry for it.
4. Register GitHub trusted publishing on PyPI for this repository, using workflow file `release.yml` (the workflow stored at `.github/workflows/release.yml`) and environment `pypi`.
5. Confirm the trusted publisher is scoped to this repository and the `gutenbit` project.

## Release procedure

1. Merge the desired changes into `main`.
2. Run the local verification suite:

   ```bash
   uv run pytest
   uv run ruff check .
   uv run ruff format --check .
   uv run ty check
   uv build
   uvx --from twine twine check --strict dist/*
   uvx --refresh --from dist/*.whl gutenbit --help
   uvx --refresh --from dist/*.whl gutenbit --version
   ```

3. Create and push a tag in the exact format `vX.Y.Z` that points at the chosen `main` commit.
4. Let the `Release` workflow (`.github/workflows/release.yml`) build the sdist and wheel, run `twine check`, and smoke-test the installed wheel.
5. Approve the pending deployment for the `pypi` environment.
6. Let the workflow publish the verified artifacts to PyPI using trusted publishing.
7. Let the workflow attach the same verified wheel and sdist to the GitHub release.
8. Let the `docs` GitHub Actions workflow publish the tagged docs build to the public site.

## Notes

Stable users should install with `uv tool install gutenbit` or add the library with `uv add gutenbit`.
Installs from `main` or from GitHub URLs are development builds, not stable releases.
Docs pushes on `main` are validated but not deployed.
If you need to republish docs for an existing release, run the `Manual Docs Deploy` workflow against that release tag.
Do not edit version strings in source files; `hatch-vcs` derives release versions from the Git tag.

The GitHub Pages custom domain is tracked in `docs/CNAME` so every docs deploy preserves `gutenbit.textualist.org`.
