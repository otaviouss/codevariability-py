# Python publication checklist

Released version: **0.2.0**. Distribution: **codevariability**.
Import/CLI: **codevariability**. A checked item records an executed check;
unchecked items are required before the first public release.

- [x] Public source and selected behavior tests reviewed.
- [x] README, API, metric, format, and release documentation reviewed.
- [x] MIT license and original copyright preserved.
- [x] Metadata, distribution name, import name, and single-source version checked.
- [x] Dependencies resolved and checked for known vulnerabilities.
- [x] Independent Python tests and lint passed.
- [x] Run repository CI on the final remediated commit (Python 3.10 and 3.12).
- [x] Clean wheel and sdist builds completed.
- [x] Every wheel and sdist member inspected.
- [x] Wheel installed in a fresh environment; import and CLI checked.
- [x] Sdist rebuilt independently.
- [x] README code and all bundled examples executed.
- [x] New files checked for secrets, local paths, and internal materials.
- [x] No research inputs, manuscript files, caches, or generated results included.
- [x] New repository URLs verified with authenticated access.
- [x] PyPI project-name existence checked; recheck ownership/version before upload.
- [x] Make the GitHub repository public and verify links without authentication.
- [x] Configure the PyPI trusted publisher and publish through GitHub Actions OIDC.
- [x] Publish 0.2.0 on 2026-10-03; verify registry hashes, fresh install, CLI, and all examples.

The historical local implementation is the baseline; this distribution is
the first public PyPI release. See
[docs/RELEASING.md](docs/RELEASING.md).

Published package: https://pypi.org/project/codevariability/0.2.0/

GitHub release: https://github.com/otaviouss/codevariability-py/releases/tag/v0.2.0

Trusted publisher: owner `otaviouss`, repository `codevariability-py`,
workflow `release.yml`, environment `pypi`. The PyPI distribution name is
`codevariability`, selected by the maintainer before its first publication.
