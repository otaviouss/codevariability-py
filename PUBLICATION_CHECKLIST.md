# Python publication checklist

Prepared version: **0.2.0**. Distribution: **codevariability-py**.
Import/CLI: **codevariability**. A checked item records an executed check;
unchecked items are required before the first public release.

- [x] Public source and selected behavior tests reviewed.
- [x] README, API, metric, format, and release documentation reviewed.
- [x] MIT license and original copyright preserved.
- [x] Metadata, distribution name, import name, and single-source version checked.
- [x] Dependencies resolved and checked for known vulnerabilities.
- [x] Independent Python tests and lint passed.
- [x] Python CI passed in the new repository.
- [x] Clean wheel and sdist builds completed.
- [x] Every wheel and sdist member inspected.
- [x] Wheel installed in a fresh environment; import and CLI checked.
- [x] Sdist rebuilt independently.
- [x] README code and all bundled examples executed.
- [x] New files checked for secrets, local paths, and internal materials.
- [x] No research inputs, manuscript files, caches, or generated results included.
- [x] New repository URLs verified with authenticated access.
- [x] PyPI project-name existence checked; recheck ownership/version before upload.
- [ ] Make the GitHub repository public and verify links without authentication.
- [ ] Configure registry authentication or a trusted publisher for this new project.
- [ ] Choose the release date, publish, and verify installation from the registry.

Do not install this distribution alongside the historical `codevariability`
distribution, which provides the same import and command. See
[docs/RELEASING.md](docs/RELEASING.md).
