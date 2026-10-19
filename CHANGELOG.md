# Changelog

All notable changes to RouteGIS AutoBuilder are documented here. This project follows [Semantic Versioning](https://semver.org/).

The public repository starts with version 0.2.3. Earlier entries describe the application's development history.

## [Unreleased]

### Changed

- Fetch up to three provider groups concurrently, with one owned client and sequential layers/tiles per provider. Preserve Overpass companion-response reuse, selection/report order, monotonic progress and caller-thread callbacks/validation/export. Stop follow-up requests and close all workers before cancellation removes staging; injected providers remain sequential and caller-owned.
- Summarize routine download progress after five seconds or 25 additional requests, plus exact final counters. Preserve retry/error/recovery/budget-change messages and check cancellation independently of log throttling.

### Fixed

- Let successful VWorld WFS/Ecology saturation responses and VWorld Data API pagination reserve additional requests beyond the initial tile-based estimate, while retaining the per-layer absolute cap, counted retries and atomic failure semantics. Keep outage-only retries bounded by the existing estimate and recovery limits.
- Use VWorld's reported match count to skip up to four levels of saturated intermediate WFS queries, without exceeding the server's 1,000-feature request limit or changing geometry validation and deduplication.
- Accept the Ecology service's official GML 2 EPSG URL identifiers and optional per-feature `gml:boundedBy` metadata. Keep mismatched/unknown CRS identifiers, foreign properties and malformed authoritative geometry rejected.
- Recover invalid VWorld source polygons whose spikes collapse to zero-area lines/points during `make_valid`, preserving every polygon component and logging repaired feature IDs. Still reject wrong-family source geometry and wholly collapsed polygons.

## [0.3.0] - 2026-10-19

### Added

- Generate a self-contained `<project>.html` alongside QGZ/GeoPackage/Shapefile output. Embed Leaflet 1.9.4 (BSD-2-Clause), CSS, images, styles, vector data and notices; viewing requires neither QGIS, qgis2web, a CDN nor companion project files.
- Include derived linework, the analysis buffer and selected successful spatial layers, but exclude raw/source CAD. Support zoom/pan, layer controls, batched Canvas rendering, property popups and source/license metadata. Distinguish failed and empty layers, preserve geometry, and keep optional VWorld backgrounds online-only.
- Open generated web maps on the VWorld general map by default, with satellite and offline (no basemap) options still selectable.
- Expose the web map in Wizard results and an HTML-open button, CLI output and Manifest/report outputs. Keep export cancellable and atomic with the existing project publication flow.

### Fixed

- Accept the Ecology service's observed `kr.nie-ecobank` feature namespace alongside `http://openapi.nie.re.kr`, while preserving strict feature, property, geometry and CRS validation.
- Retry VWorld HTTP-200 XML/JSON connection-pool timeout errors and honor bounded numeric `Retry-After` delays. Subdivide only the overloaded WFS/Overpass/Ecology tile after transient failures; retain completed tiles, deduplicate features and keep failures atomic. Bound outage subdivision to depth 4 in addition to existing size/depth/request limits.
- Reuse the successfully subdivided power-query plan and byte-bounded responses for the companion power layer; do not repeat failed parent queries or reuse incomplete downloads.
- Leave the standalone QGIS smoke process without finalizing CPython. Finalization destroyed QGIS objects after `exitQgis()` and faulted in `Qt5Core.dll`; QGIS Desktop is unaffected because it never finalizes embedded Python. The smoke also reports each section so a failure shows where it stopped.

### Changed

- Start drawing analysis explicitly on Wizard page two, not during Next-page navigation. Run DXF parsing, inventory and CRS recommendations in a cancellable background task, with progress, elapsed time and a log above the map.
- Build the native QGIS preview in 500-feature GUI-event batches. Block navigation while analysis/preview work is active and reject stale results after drawing changes or plugin unload.
- Emit per-tile request/retry/recovery progress during provider downloads and per-entity DXF scan progress during project builds.

## [0.2.9] - 2026-10-16

### Fixed

- Restore Windows system-trust TLS for VWorld, Ecology and Overpass (regression from 0.2.8 which dropped the 0.2.4 `tls.py` fix). Combine httpx/certifi defaults with `load_default_certs()`, explicit `certifi` bundle, `SSL_CERT_FILE`/`SSL_CERT_DIR` (QGIS bundle) and `ROUTEGIS_CA_BUNDLE`/`ROUTEGIS_CA_DIR` overrides so corporate HTTPS-inspection CAs trusted by Windows verify correctly on any company PC.
- Detect corporate proxies for httpx: explicit `HTTPS_PROXY`/`HTTP_PROXY` env (via `trust_env`), QGIS Settings proxy (`설정 > 옵션 > 네트워크`) and Windows registry proxy via `urllib.request.getproxies()`, so QGIS-configured PCs no longer fail with `CERTIFICATE_VERIFY_FAILED` through an intercepting proxy.
- Fail fast on certificate verification errors without retrying the same TLS handshake or sleeping; report Windows Trusted-Root registration guidance, QGIS restart, proxy check and `ROUTEGIS_CA_BUNDLE` option while keeping API keys redacted.
- Keep Ecology feature errors debuggable: report `예상 tbl_opn_eczm, 실제 <tag>` with the actual tag instead of a `[redacted URL]`-mangled `{namespace}name`, and only redact URLs carrying a query string so the `openapi.nie.re.kr` namespace stays visible.
- Add `tests/integration/test_provider_tls.py` regression coverage (system-trust context, env/QGIS proxy resolution, per-provider cert fail-fast, ecology message).

## [0.2.8] - 2026-10-13

### Changed

- Remove river centerline from the shared CLI/Wizard default profile, leaving six default layers; it remains available for manual selection.
- Reuse successful power-query tile responses once for the following companion layer (lines or towers/poles), reducing default OSM downloads from four HTTP requests to two per tile when responses fit the cache budget and no retries are needed.
- Bound the retained response set by the existing response-size limit, discard it on failure or close, and count only actual HTTP transmissions in download statistics.
- Separate river-area and river-centerline queries so each downloads only the requested dataset; preserve the existing 180-second timeout, three-attempt ceiling and server failover policy.
- Verify optional selection and reduced request counts through installed-QGIS widgets, plus multi-tile reuse, retry accounting, cache bounds and failed-response isolation in provider tests.

## [0.2.7] - 2026-10-13

### Added

- Recommend Korean projected CRS candidates from actual DXF linework extents and the Wizard's business province/city, accepting historical and current administrative names and explaining ambiguous candidates.
- Automatically overlay DXF linework on the same VWorld Street/Hybrid basemaps as `11_기본도` in the drawing-analysis step, with CRS switching, estimated coordinates, pan/zoom and a separate CAD inventory tab.
- Keep preview layers in a temporary QGIS project, clear stale results when inputs change, and verify coordinate reinterpretation, raster/line rendering and cleanup in installed QGIS.

### Changed

- Centralize response-size limits, request budgets, tile tracking and download-result statistics shared by VWorld, Ecology and Overpass while preserving provider-specific parsing and errors.
- Replace Wizard source-shape assertions with behavior checks, including selection edits during live catalog loading.

### Fixed

- Preserve the latest layer selections when a background VWorld catalog refresh finishes instead of restoring the selections captured before the request.
- Derive the Overpass User-Agent from the bundled core version so requests identify the installed release correctly.
- Enable on-the-fly reprojection in background-generated QGIS projects so QGIS retains the project CRS and layer CRS changes move linework correctly on the map.
- Import the bundled core through the plugin's own namespace so an in-session QGIS upgrade cannot combine the new Wizard with an older cached `routegis` service and fail with `build() got an unexpected keyword argument 'on_progress'`.
- Exercise the real build task with an incompatible global core already loaded, and check the actual project-generation button after upgrades and plugin reloads in installed QGIS.

## 0.2.6

### Added

- Run Wizard project generation in a background QGIS task with live stage progress, elapsed time, time since the last progress event and timestamped per-layer logs.
- Allow cooperative cancellation after the current operation/request, preserving existing project outputs and cleaning up staging files.
- Verify UI responsiveness during delayed provider responses, main-thread progress delivery, cancellation and worker failures in installed-QGIS smoke tests.

### Fixed

- Recover from busy Overpass servers through the public Private.coffee and VK Maps mirrors within the existing three-attempt limit, reuse the working server for subsequent layers, retry temporary HTTP 500 errors, and explain exhausted server failures; keep custom endpoints pinned.
- Use the existing headless QGIS project writer in background tasks so worker threads do not create or access QGIS GUI objects; open the finished project on the main thread.
- Preserve live logs on completion and restore Wizard controls on success, failure or cancellation.

## 0.2.5

### Fixed

- Use the Wizard's entered or saved API keys ahead of environment and plugin `.env` credentials for both catalog loading and project generation; retain environment fallback for empty fields.
- Accept both Encoding and Decoding public-data service keys without double URL encoding, and redact both forms from errors and output artifacts.
- Show each layer's name, provider, feature count and detailed failure reason in the Wizard, including an explicit all-failed summary and report/log paths.
- Preserve VWorld error codes and public-data gateway authentication reasons, including HTTP 403 responses; explain Overpass certificate, DNS, proxy and timeout failures.
- Extend regression coverage through actual QGIS widgets and all three providers' HTTP request paths using controlled responses.

## [0.2.3] - 2026-10-02

### Changed

- Append `(해당없음)` to a project group when it contains vector layers and all of their feature counts are zero.
- List all 28 supported Korean projected CRS presets by EPSG code and name, and initialize the Wizard output CRS from the selected source CRS.
- Move the land group directly above the basemaps, renumber all groups in display order and set land-ownership layer opacity to 70% in both native and CLI-generated QGIS projects.
- Use every valid DXF `LINE`, `POLYLINE`, `LWPOLYLINE` and `ARC` object as the Wizard analysis-area source without asking for a project type or route-layer selection.
- Remember API keys entered in the Wizard for later sessions while preserving environment and plugin `.env` precedence.
- Remove temporary-input wording and the VWorld registered-domain field from the desktop Wizard.
- Support the Python 3.9 runtime included with QGIS 3.34.3.
- Re-license the project under the MIT License.

### Fixed

- Recalculate exported CAD `length_m` values after reprojection so GeoPackage and Shapefile attributes match the output geometry and manifest.
- Avoid GEOS `bad allocation` on dense CAD drawings by buffering individual lines before dissolving their analysis areas, while preserving every source feature.
- Require the Python compatibility tests and packaged security checks before preparing GitHub release assets, and verify the installed CLI wheel and downloadable checksums.

## 0.2.1 - 2026-10-01

### Added

- Reproducible, self-contained QGIS plugin archives for official repository and GitHub distribution.
- Installed-QGIS smoke coverage for plugin loading, project output, styling and secret redaction.

### Fixed

- Preserve the default profile order when additional VWorld layers are selected.
- Retry transient Windows directory replacement failures while publishing a completed project.

## 0.2.0 - 2026-09-30

### Added

- VWorld, National Institute of Ecology and OpenStreetMap providers with tiled downloads.
- Seven default spatial layers, source attribution and provider-specific styling.
- QGIS wizard support for provider credentials and live VWorld catalog loading.

## 0.1.1 - 2026-09-23

### Fixed

- Hardened VWorld catalog and project generation behavior after live QGIS validation.

## 0.1.0 - 2026-09-22

### Added

- Initial DXF-to-QGIS project pipeline, CLI and QGIS wizard.

[0.2.8]: https://github.com/timetreess/routegis-autobuilder/releases/tag/v0.2.8
[0.2.7]: https://github.com/timetreess/routegis-autobuilder/releases/tag/v0.2.7
[0.2.3]: https://github.com/timetreess/routegis-autobuilder/releases/tag/v0.2.3
