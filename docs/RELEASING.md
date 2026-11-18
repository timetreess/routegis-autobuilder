# Release guide

## GitHub preflight (no release is published)

The **GitHub Release** workflow supports a manual run that exercises the release preparation path without creating a tag or publishing a release:

```powershell
gh workflow run release.yml --ref main
gh run list --workflow release.yml --event workflow_dispatch --limit 5
gh run watch <run-id> --exit-status
gh run download <run-id> --name release-artifacts --dir release-download
```

The workflow requires all of the following to succeed:

1. The shared CI suite on Python 3.9 and 3.12.
2. Plugin version stamping into the staged `metadata.txt` and vendored dependency closure checks.
3. Bandit, detect-secrets and pip-audit checks on the packaged plugin.
4. Installation of the core package in a clean environment and an offline fixture build (`scripts/fixture_build.py`) with all six default layers, QGZ, HTML web map, GeoPackage and Shapefile output.
5. Generation and verification of SHA-256 checksums for every release asset.

The release preparation job reuses the exact plugin ZIP that passed the security checks. A manual run leaves **Publish tagged release** skipped and uploads `release-artifacts` for download from the Actions run.

The artifact contains:

- `routegis_autobuilder.<version>.zip`: installable QGIS plugin.
- `routegis_autobuilder.<version>.zip.sha256`: plugin checksum.
- `plugins.xml`: QGIS repository metadata pointing to the eventual versioned GitHub Release.
- `SHA256SUMS`: checksums using download-relative filenames; on Linux, run `sha256sum --check SHA256SUMS` from the download directory.

Before publication, the URLs in `plugins.xml` will not resolve to downloadable release assets. In a private GitHub repository, releases and artifacts are available only to users with repository access; the generated unauthenticated QGIS feed is not a public distribution channel.

## Installed-QGIS smoke check

Run the existing smoke script against the extracted release ZIP with QGIS 3.34.3's Python. This checks plugin loading, the Wizard, fixture project generation, styles and credential redaction. It also exercises all three providers through the Wizard with controlled HTTP responses, including credential precedence, encoded service keys and all-failed diagnostics. Background builds are checked for responsive UI timers, live progress, cancellation and error recovery. For a standard Windows installation:

```powershell
python -m zipfile -e release-download/routegis_autobuilder.<version>.zip build/release-qgis-smoke
& "C:\Program Files\QGIS 3.34.3\bin\python-qgis.bat" tests/manual/qgis_smoke.py --project-root . --plugin-dir build/release-qgis-smoke/routegis_autobuilder
```

The smoke check uses offline fixtures and isolated settings. It also checks the six default selections, manually selectable river centerline, two OSM requests for the three default OSM layers, selection edits during catalog refresh, province-based CRS recommendations, explicit background drawing analysis, source-CRS and Street/Hybrid switching, unchanged raw geometry, stale-preview cleanup and raster/line rendering with a georeferenced local background. A generated 80 km / 20,000-object DXF checks immediate page navigation, UI heartbeat, live analysis progress, full preview preservation and cancellation. Live XYZ tile availability and provider credentials are separate from this check.

Also run the upgrade check using an extracted plugin ZIP from the previous GitHub Release. It loads the older plugin, unloads it through QGIS, replaces its files in the same temporary installation directory, and clicks **프로젝트 생성** without restarting Python. The new plugin must use its own bundled core, reach 100%, open the generated project, and build again after a QGIS plugin reload:

```powershell
& "C:\Program Files\QGIS 3.34.3\bin\python-qgis.bat" tests/manual/qgis_smoke.py --project-root . --plugin-dir build/release-qgis-smoke/routegis_autobuilder --previous-plugin-dir build/release-qgis-v<previous-version>/routegis_autobuilder
```

Keep the older installation unchanged for this check; rebuilding an old version number from current sources does not reproduce an actual upgrade.

The process must exit with code 0. `python-qgis.bat` finalizes the interpreter on the
way out, unlike QGIS Desktop, which never imports `Py_Finalize`. CPython then
destroys QGIS objects that are still reachable through reference cycles, which
happens after `QgsApplication.exitQgis()` has already torn the runtime down and
faults with `0xC0000005` in `Qt5Core.dll`. Keeping a second `QgsProject` or a
`QgsMapCanvas` alive reproduces it in 3 out of 3 runs; releasing the same objects
before exiting does not. The smoke therefore leaves through `os._exit()` after its
assertions, and prints a `stage:` line per section so a failing run shows where it
stopped.

## Standalone HTML browser smoke check

Every project build also generates a single `<project>.html`. Build the sample
project from a development environment (see [CONTRIBUTING.md](../CONTRIBUTING.md)),
then test the HTML in a clean browser context with no companion files:

```powershell
uv run --no-project --with playwright playwright install chromium
python scripts/fixture_build.py build/fixture-output
uv run --no-project --with playwright tests/manual/web_map_smoke.py "build/fixture-output/release-smoke/release-smoke.html"
```

The check copies only the HTML to a temporary folder and verifies embedded feature
counts, zoom, pan, layer toggles, popups and disabled failed/empty layers. The map
opens on the VWorld general map, so the check first confirms those requests go to
`xdworld.vworld.kr`, then switches the basemap off and requires that the embedded
data still works with zero further HTTP requests. Use `--channel msedge` to check an
installed Edge where automation is permitted.

## Publish a GitHub Release

After the preflight and both smoke checks pass:

1. Set the new version only in `__version__` in `src/routegis/__init__.py`. `pyproject.toml`, the plugin ZIP's `metadata.txt` (the repository copy holds a `0.0.0` placeholder), the smoke check and the release tag check all read it from there. Update `CHANGELOG.md` and the `changelog=` line in `plugin/metadata.txt`.
2. Merge the intended changes to `main`, run the preflight and the smoke checks above, and confirm the tested commit is the intended release commit.
3. Create and push the version tag at that commit, replacing `<version>` (for example `0.3.5`):

   ```powershell
   git switch main
   git pull --ff-only origin main
   git tag v<version> <tested-commit-sha>
   git push origin v<version>
   ```

4. Monitor **GitHub Release**. A tag push reruns the preflight, verifies the tag matches the package version, then publishes the checked assets. No separate GitHub publishing secret is required; the publish job uses its scoped `GITHUB_TOKEN`.
5. Confirm the release assets and installation instructions at `https://github.com/timetreess/routegis-autobuilder/releases`.

## Optional official QGIS repository publication

The **Publish QGIS Plugin** workflow is a separate manual action. It requires an existing GitHub Release, a valid `QGIS_TOKEN` repository/environment secret, and access to the `routegis_autobuilder` entry at the official QGIS plugin repository. The workflow uses the `qgis-plugin-repository` environment. Configure that target and its credentials before running it; they are not prerequisites for GitHub-only releases.
