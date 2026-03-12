# Releasing Gutenbit

Gutenbit uses tag-driven versioning via `hatch-vcs`. Do not edit version strings in source files.

## Procedure

1. Merge the desired changes into `main`.
2. Create a GitHub release or tag in the exact format `vX.Y.Z` that points at the chosen `main` commit.
3. Let the `Release` GitHub Actions workflow build the sdist and wheel, smoke-test the wheel, and attach the artifacts to the GitHub release.

Installs from `main` are development builds, not stable releases. PyPI publication is intentionally not part of this procedure yet.
