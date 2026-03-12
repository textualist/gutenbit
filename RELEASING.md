# Releasing gutenbit

gutenbit uses tag-driven versioning via `hatch-vcs`. Do not edit version strings in source files.

## Procedure

1. Merge the desired changes into `main`.
2. Create a GitHub release or tag in the exact format `vX.Y.Z` that points at the chosen `main` commit.
3. Let the `Release` GitHub Actions workflow build the sdist and wheel, smoke-test the wheel, and attach the artifacts to the GitHub release.
4. Let the `docs` GitHub Actions workflow publish the tagged docs build to the public site.

Installs from `main` are development builds, not stable releases. Docs pushes on `main` are validated but not deployed. If you need to republish docs for an existing release, run the `Manual Docs Deploy` workflow against that release tag. PyPI publication is intentionally not part of this procedure yet.

The GitHub Pages custom domain is tracked in `docs/CNAME` so every docs deploy preserves `gutenbit.textualist.org`.
