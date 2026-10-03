# Python publication checklist

Prepared version: **0.2.0**. Distribution: **codevariability-py**.
Import/CLI: **codevariability**. A checked item records an executed check;
unchecked items are required before the first public release.

- [ ] Public source and selected behavior tests reviewed.
- [ ] README, API, metric, format, and release documentation reviewed.
- [ ] MIT license and original copyright preserved.
- [ ] Metadata, distribution name, import name, and single-source version checked.
- [ ] Dependencies resolved and checked for known vulnerabilities.
- [ ] Independent Python tests and lint passed.
- [ ] Python CI passed in the new repository.
- [ ] Clean wheel and sdist builds completed.
- [ ] Every wheel and sdist member inspected.
- [ ] Wheel installed in a fresh environment; import and CLI checked.
- [ ] Sdist rebuilt independently.
- [ ] README code and all bundled examples executed.
- [ ] New files checked for secrets, local paths, and internal materials.
- [ ] No research inputs, manuscript files, caches, or generated results included.
- [ ] New repository URLs verified with authenticated access.
- [ ] PyPI project-name existence checked; recheck ownership/version before upload.
- [ ] Make the GitHub repository public and verify links without authentication.
- [ ] Configure registry authentication or a trusted publisher for this new project.
- [ ] Choose the release date, publish, and verify installation from the registry.

Do not install this distribution alongside the historical `codevariability`
distribution, which provides the same import and command. See
[docs/RELEASING.md](docs/RELEASING.md).
