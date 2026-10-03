# Releasing the Python package

The repository is public. Confirm that the metadata URLs and issue tracker
remain reachable by users before each release.
The target distribution is `codevariability`; its import remains
`codevariability`. Version 0.2.0 was first published on 2026-10-03 from
this repository through PyPI Trusted Publishing. The historical 0.1.2 source is its implementation baseline;
there is no new published release history implied by that baseline.

1. Update `src/codevariability/__init__.py`; setuptools reads that version
   without a second version field. Update CHANGELOG and the public checklist.
2. Start from a clean checkout and a new virtual environment. Install `.[dev]`,
   upgrade pip and setuptools (>=83.0.0), run Ruff, mypy, pytest, and all scripts
   under `examples/` after installation.
3. Run `python -m build` and `python -m twine check --strict dist/*`.
4. Inspect every wheel/sdist member. A wheel contains only `codevariability`
   and distribution metadata/license; the sdist also contains the reviewed
   tests, examples, and user documentation. Do not reuse a directory of old
   distribution files.
5. Install the wheel into another environment, check dependencies, and run
   the bundled examples. Also confirm that the sdist builds independently.
6. Recheck package-name ownership, version availability, dependency
   vulnerabilities, secrets, metadata, and links immediately before upload.
7. Configure a PyPI trusted publisher with owner `otaviouss`, repository
   `codevariability-py`, workflow filename `release.yml`, and environment
   `pypi`. For a first release, create a pending publisher for project
   `codevariability`. Never store registry credentials in this repository.
8. Dispatch `.github/workflows/release.yml` from `main` with `publish=false`
   to validate and build only. With `publish=true`, the separate publishing
   job exchanges its GitHub OIDC identity and uploads the validated artifacts
   to production PyPI. Only that job has `id-token: write`.
9. Verify registry hashes and a fresh installation, then create the matching
   GitHub release. Keep publication evidence separate from earlier audits.

TestPyPI has a separate account and publisher configuration. When validating
its package, install dependencies from production PyPI first, then install
`codevariability==0.2.0` with `--no-deps --index-url
https://test.pypi.org/simple/`. This avoids mixing dependency resolution across
the two indexes. Ordinary CI performs checks only; the manually dispatched
release workflow can publish to production PyPI.

The Python and JavaScript projects have separate release schedules. Changing
one project's release version need not change the other, but interchange
schema, metric IDs, and preprocessing versions must remain explicit.

Before releasing this candidate, run Ruff and `mypy` as well as the original,
adversarial, and regression tests on Python 3.10 and 3.12. Test both wheel and
sdist installations, current dependency resolution, and supported runtime
minimums. Build isolation must honor setuptools >=83.0.0. The dev extra adds
Hypothesis and mypy; neither is required by runtime consumers.
