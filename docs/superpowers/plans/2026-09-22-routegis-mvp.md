# RouteGIS AutoBuilder MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the PRD's P0 RouteGIS AutoBuilder vertical slice as a QGIS plugin backed by a headless-testable Python core.

**Architecture:** A deterministic pure-Python core owns CAD parsing, CRS/geometry operations, VWorld download, storage, reporting, and project specification. A thin QGIS plugin supplies the wizard and uses PyQGIS when available; the headless CLI emits a conservative QGZ for testing when QGIS is unavailable. GeoPackage is authoritative and SHP is compatibility output.

**Tech Stack:** Python 3.10+, ezdxf, Shapely, pyproj, HTTPX, PyYAML, PyQGIS/PyQt in the plugin runtime, pytest.

---

## Scope Decisions

- Implement every P0 item in `PRD.md` section 60.
- Include one static "토목 노선 기본" profile and minimum route/AOI/vector styling because section 52 requires them for acceptance, although section 60 labels the general systems P1.
- Exclude cache, large-AOI tiling, AI recommendations, PDF/image input, and Phase 2+ analysis.
- Require explicit source CRS confirmation. Never infer and silently accept a CRS.
- Require a projected metre-based output CRS for distance buffering.
- Support DWG only through a configured external converter adapter.
- Preserve successful layers and produce `COMPLETED_WITH_WARNINGS` when an individual VWorld layer fails.
- Pin the headless QGZ template to QGIS 3.40 format; prefer native PyQGIS project writing inside QGIS.

## Deliverable Structure

- Create `src/routegis/` for models, configuration, CAD, GIS, provider, project, reporting, orchestration, and CLI code.
- Create `plugin/` for QGIS lifecycle and wizard UI only.
- Create package data under `src/routegis/resources/` for `config.yaml`, catalog, profile, and QML styles so installed wheels and the QGIS runtime use identical resources.
- Create `tests/unit/`, `tests/integration/`, and `tests/fixtures/` for high-risk boundaries and the golden DXF.
- Keep `qgis_automation.py` as a compatibility launcher that points users to the new CLI.

### Task 1: Package and Configuration Foundation

**Files:**
- Create: `pyproject.toml`
- Create: `src/routegis/resources/config.yaml`
- Create: `src/routegis/resources/layer_catalog.yaml`
- Create: `src/routegis/resources/presets/basic_route.yaml`
- Create: `src/routegis/resources/styles/*.qml`

- [ ] Define Python/package metadata and runtime/test dependencies.
- [ ] Externalize defaults, catalog identifiers, request CRS, profile selection, and style files.
- [ ] Verify YAML files load and contain no credential values.

### Task 2: Domain Contracts

**Files:**
- Create: `src/routegis/models.py`
- Create: `src/routegis/config.py`
- Create: `src/routegis/errors.py`

- [ ] Model `ProjectConfig`, `RouteGeometry`, `SpatialFeature`, `SpatialLayer`, `LayerDefinition`, `DownloadResult`, and pipeline states.
- [ ] Validate names, paths, CRS strings, distances, selected layers, and external catalog shape.
- [ ] Keep secrets out of serializable models and manifests.

### Task 3: CAD Route Processing

**Files:**
- Create: `src/routegis/cad/reader.py`
- Create: `src/routegis/cad/converter.py`
- Create: `src/routegis/cad/route_detector.py`

- [ ] Read DXF LINE, POLYLINE, LWPOLYLINE, and ARC entities with ezdxf.
- [ ] Flatten arcs/bulges using source-unit chord tolerance.
- [ ] Inventory layers and score route candidates by keyword, type, length, count, and continuity.
- [ ] Deterministically snap endpoints and reject branches or unresolved disconnected selections.
- [ ] Invoke an external DWG converter with actionable missing/failed-converter errors.

### Task 4: CRS, AOI, and QA

**Files:**
- Create: `src/routegis/gis/crs.py`
- Create: `src/routegis/gis/geometry.py`
- Create: `src/routegis/gis/validator.py`

- [ ] Transform with pyproj `always_xy=True` and preserve explicit CRS metadata.
- [ ] Reject metre buffers in angular CRS and build the configured route buffer.
- [ ] Validate empties, validity, duplicates, geometry type, route length/continuity, and AOI.
- [ ] Transform bounds to WGS84 for Korean-range sanity checks and warnings.

### Task 5: VWorld Adapter

**Files:**
- Create: `src/routegis/providers/base.py`
- Create: `src/routegis/providers/vworld/catalog.py`
- Create: `src/routegis/providers/vworld/client.py`

- [ ] Build requests only from catalog definitions and AOI bounds.
- [ ] Paginate with connection reuse, timeout, bounded retry/backoff, repeated-page protection, and max-pages.
- [ ] Parse and validate GeoJSON, deduplicate by provider ID or canonical content hash, and redact keys from errors/logs.
- [ ] Return per-layer results so one failure cannot discard other data.

### Task 6: GeoPackage and Shapefile Output

**Files:**
- Create: `src/routegis/gis/gpkg.py`
- Create: `src/routegis/gis/shapefile.py`
- Create: `src/routegis/gis/export.py`

- [ ] Write GeoPackage 1.3 core metadata, SRS definitions, geometry blobs, attributes, extents, and deterministic FIDs.
- [ ] Write constrained 2D SHP/SHX/DBF/PRJ/CPG datasets and deterministic field-name mappings.
- [ ] Reject unsupported mixed/collection geometries instead of silently coercing them.

### Task 7: QGIS Project, Report, and Orchestration

**Files:**
- Create: `src/routegis/qgis/project.py`
- Create: `src/routegis/report.py`
- Create: `src/routegis/app/project_service.py`

- [ ] Create the PRD output directory tree and copy the source drawing.
- [ ] Run CAD, CRS, AOI, VWorld, QA, export, project, manifest, report, and logging stages in order.
- [ ] Emit grouped relative-path layers, embedded styles, project CRS, and initial route extent in `.qgz`.
- [ ] Prefer native PyQGIS writing when available and fall back to headless QGS-in-QGZ generation.
- [ ] Write deterministic manifest and HTML/JSON reports without secrets.

### Task 8: CLI and QGIS Wizard

**Files:**
- Create: `src/routegis/cli.py`
- Modify: `qgis_automation.py`
- Create: `plugin/metadata.txt`
- Create: `plugin/__init__.py`
- Create: `plugin/routegis_plugin.py`
- Create: `plugin/wizard.py`

- [ ] Add `analyze` and `build` CLI commands with clear Korean-facing errors.
- [ ] Build QGIS plugin lifecycle actions and wizard pages for project, CAD route, CRS/AOI, layers, and progress/result.
- [ ] Store recent non-secret settings only; never persist the API key in the project.

### Task 9: High-Risk Tests and Golden Workflow

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/unit/test_cad.py`
- Create: `tests/unit/test_crs_geometry.py`
- Create: `tests/unit/test_storage.py`
- Create: `tests/integration/test_vworld.py`
- Create: `tests/integration/test_pipeline.py`
- Create: `tests/fixtures/sample_route.dxf`

- [ ] Verify DXF extraction/arc flattening, snapping, CRS transforms, buffers, QA, catalog, pagination/retry/dedup, and partial failures.
- [ ] Run the golden workflow from sample DXF through mock VWorld to GPKG, SHP, QGZ, report, and manifest.
- [ ] Inspect container/table/XML/binary metadata independently rather than trusting the writers.

### Task 10: Documentation and Acceptance

**Files:**
- Create: `README.md`

- [ ] Document QGIS plugin installation, Python setup, CLI use, DWG adapter, credentials, output tree, and known runtime constraints.
- [ ] Run diagnostics, pytest, package build, CLI help, bad-input path, and headless golden build.
- [ ] Inspect every acceptance artifact and scan outputs/logs for API-key leakage.
- [ ] Record the unresolved external gates: real QGIS open smoke test, real DWG converter, and credentialed VWorld smoke test.
