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
2. Plugin version consistency and vendored dependency closure checks.
3. Bandit, detect-secrets and pip-audit checks on the packaged plugin.
4. Python wheel and source distribution builds, including strict Twine metadata validation.
5. Installation of the built wheel in a clean environment and an offline CLI build with all six default layers, QGZ, GeoPackage and Shapefile output.
6. Generation and verification of SHA-256 checksums for every release asset.

The release preparation job reuses the exact plugin ZIP that passed the security checks. A manual run leaves **Publish tagged release** skipped and uploads `release-artifacts` for download from the Actions run.

The artifact contains:

- `routegis_autobuilder.<version>.zip`: installable QGIS plugin.
- `routegis_autobuilder.<version>.zip.sha256`: plugin checksum.
- `plugins.xml`: QGIS repository metadata pointing to the eventual versioned GitHub Release.
- `routegis_autobuilder-<version>-py3-none-any.whl`: CLI wheel.
- `routegis_autobuilder-<version>.tar.gz`: Python source distribution.
- `SHA256SUMS`: checksums using download-relative filenames; on Linux, run `sha256sum --check SHA256SUMS` from the download directory.

Before publication, the URLs in `plugins.xml` will not resolve to downloadable release assets. In a private GitHub repository, releases and artifacts are available only to users with repository access; the generated unauthenticated QGIS feed is not a public distribution channel.

## Installed-QGIS smoke check

Run the existing smoke script against the extracted release ZIP with QGIS 3.34.3's Python. This checks plugin loading, the Wizard, fixture project generation, styles and credential redaction. It also exercises all three providers through the Wizard with controlled HTTP responses, including credential precedence, encoded service keys and all-failed diagnostics. Background builds are checked for responsive UI timers, live progress, cancellation and error recovery. For a standard Windows installation:

```powershell
python -m zipfile -e release-download/routegis_autobuilder.0.2.9.zip build/release-qgis-smoke
& "C:\Program Files\QGIS 3.34.3\bin\python-qgis.bat" tests/manual/qgis_smoke.py --project-root . --plugin-dir build/release-qgis-smoke/routegis_autobuilder
```

The smoke check uses offline fixtures and isolated settings. It also checks the six default selections, manually selectable river centerline, two OSM requests for the three default OSM layers, selection edits during catalog refresh, province-based CRS recommendations, explicit background drawing analysis, source-CRS and Street/Hybrid switching, unchanged raw geometry, stale-preview cleanup and raster/line rendering with a georeferenced local background. A generated 80 km / 20,000-object DXF checks immediate page navigation, UI heartbeat, live analysis progress, full preview preservation and cancellation. Live XYZ tile availability and provider credentials are separate from this check.

Also run the upgrade check using an extracted, previously installed plugin. It loads the older plugin, unloads it through QGIS, replaces its files in the same temporary installation directory, and clicks **프로젝트 생성** without restarting Python. The new plugin must use its own bundled core, reach 100%, open the generated project, and build again after a QGIS plugin reload:

```powershell
& "C:\Program Files\QGIS 3.34.3\bin\python-qgis.bat" tests/manual/qgis_smoke.py --project-root . --plugin-dir build/release-qgis-smoke/routegis_autobuilder --previous-plugin-dir build/release-qgis-v0.2.8/routegis_autobuilder
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

## Publish a GitHub Release

### Standalone HTML browser smoke check

Current development sources also generate a single `<project>.html`. Test it in a
clean browser context with all networking disabled and with no companion files:

```powershell
uv run --no-project --with playwright playwright install chromium
uv run --no-project --with playwright tests/manual/web_map_smoke.py "build/wheel-smoke-output/release-smoke/release-smoke.html"
```

The check copies only the HTML to a temporary folder and verifies embedded feature
counts, zoom, pan, layer toggles, popups and disabled failed/empty layers. The map
opens on the VWorld general map, so the check first confirms those requests go to
`xdworld.vworld.kr`, then switches the basemap off and requires that the embedded
data still works with zero further HTTP requests. Use `--channel msedge` to check an
installed Edge where automation is permitted.

After deciding to publish:

1. Keep the version consistent in `pyproject.toml`, `src/routegis/__init__.py`, `plugin/metadata.txt` and `tests/manual/qgis_smoke.py`; update `CHANGELOG.md` and the plugin changelog.
2. Merge the intended changes to `main`, run the preflight and installed-QGIS smoke check, and confirm the tested commit is the intended release commit.
3. Create and push the version tag at that commit. For version 0.2.9:

   ```powershell
   git switch main
   git pull --ff-only origin main
   git tag v0.2.9 <tested-commit-sha>
   git push origin v0.2.9
   ```

4. Monitor **GitHub Release**. A tag push reruns the preflight, verifies the tag matches the package version, then publishes the checked assets. No separate GitHub publishing secret is required; the publish job uses its scoped `GITHUB_TOKEN`.
5. Confirm the release assets and installation instructions at `https://github.com/timetreess/routegis-autobuilder/releases`.

## Optional official QGIS repository publication

The **Publish QGIS Plugin** workflow is a separate manual action. It requires an existing GitHub Release, a valid `QGIS_TOKEN` repository/environment secret, and access to the `routegis_autobuilder` entry at the official QGIS plugin repository. The workflow uses the `qgis-plugin-repository` environment. Configure that target and its credentials before running it; they are not prerequisites for GitHub-only releases.
