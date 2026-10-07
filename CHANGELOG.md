# Changelog

All notable changes to RouteGIS AutoBuilder are documented here. This project follows [Semantic Versioning](https://semver.org/).

The public repository starts with version 0.2.3. Earlier entries describe the application's development history.

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

[0.2.3]: https://github.com/timetreess/routegis-autobuilder/releases/tag/v0.2.3
