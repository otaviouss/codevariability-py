# Releasing the Python package

The repository is initially private. Before a public release, make it public
and confirm that the metadata URLs and issue tracker are reachable by users.
The target distribution is `codevariability-py`; its import remains
`codevariability`. Version 0.2.0 is prepared and has not been published from
this repository. The historical 0.1.2 source is its implementation baseline;
there is no new published release history implied by that baseline.

1. Update `src/codevariability/__init__.py`; setuptools reads that version
   without a second version field. Update CHANGELOG and the public checklist.
2. Start from a clean checkout and a new virtual environment. Install `.[dev]`,
   run Ruff, pytest, and all scripts under `examples/` after installation.
3. Run `python -m build` and `python -m twine check --strict dist/*`.
4. Inspect every wheel/sdist member. A wheel contains only `codevariability`
   and distribution metadata/license; the sdist also contains the reviewed
   tests, examples, and user documentation. Do not reuse a directory of old
   distribution files.
5. Install the wheel into another environment, check dependencies, and run
   the bundled examples. Also confirm that the sdist builds independently.
6. Recheck package-name ownership, version availability, dependency
   vulnerabilities, secrets, metadata, and links immediately before upload.
7. Configure PyPI/TestPyPI credentials locally or a project-specific trusted
   publisher. Never store credentials in this repository. Manual test upload
   uses `python -m twine upload --repository testpypi dist/*`. After checking
   that installation, the separate production action is `python -m twine
   upload dist/*`.

TestPyPI has a separate account and publisher configuration. When validating
its package, install dependencies from production PyPI first, then install
`codevariability-py==0.2.0` with `--no-deps --index-url
https://test.pypi.org/simple/`. This avoids mixing dependency resolution across
the two indexes. Publication is a maintainer action; CI performs checks only.

The Python and JavaScript projects have separate release schedules. Changing
one project's release version need not change the other, but interchange
schema, metric IDs, and preprocessing versions must remain explicit.
