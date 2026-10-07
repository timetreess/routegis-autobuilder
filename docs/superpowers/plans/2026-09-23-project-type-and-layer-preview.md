# Project Type and Layer Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let RouteGIS search every VWorld layer identifier, support non-linear all-object buffers, and let linear-project users inspect per-layer temporary Shapefiles in QGIS before selecting centerline layers.

**Architecture:** Keep topology-sensitive route assembly as the linear path and add one explicit non-linear path that preserves all supported CAD line entities as a multi-part source geometry. Keep temporary preview export separate from final project publication so previews never alter or depend on the final output directory. Store VWorld search metadata structurally on each list item rather than parsing visible labels.

**Tech Stack:** Python 3.9, PyQt5/QGIS 3.34, ezdxf, Shapely, Fiona, pytest

---

### Task 1: Reproduce VWorld `data_id` search failure

**Files:**
- Modify: `plugin/wizard.py`
- Test: `tests/unit/test_plugin_wizard.py`

- [ ] Add a focused PyQt test or importable filter helper test using a layer whose internal ID and VWorld `data_id` differ.
- [ ] Verify searching by `data_id` fails before the fix.
- [ ] Store display name, internal ID, and `data_id` in the list item's user data and include all three in the case-insensitive search haystack.
- [ ] Verify display-name, internal-ID, and `data_id` queries all pass without changing check states.

### Task 2: Model project geometry mode

**Files:**
- Modify: `src/routegis/models.py`
- Modify: `src/routegis/config.py`
- Modify: `src/routegis/cli.py`
- Test: `tests/unit/test_config.py`

- [ ] Add an explicit `project_type`/geometry-mode value with `linear` as the backward-compatible default.
- [ ] Require at least one selected source layer only for linear mode; non-linear mode derives all supported source layers from the DXF inventory.
- [ ] Add a CLI option for explicit non-linear builds while preserving existing commands.
- [ ] Verify validation accepts non-linear mode without a chosen centerline and rejects invalid mode values.

### Task 3: Build non-linear all-object geometry

**Files:**
- Modify: `src/routegis/gis/geometry.py`
- Modify: `src/routegis/app/project_service.py`
- Modify: `src/routegis/models.py`
- Test: `tests/integration/test_pipeline.py`

- [ ] Add an integration fixture with branched and disconnected supported CAD line objects across multiple layers.
- [ ] Verify the fixture fails in linear mode and currently cannot build in non-linear mode.
- [ ] For non-linear mode, collect all supported line entities, preserve them as a multi-part geometry, skip `assemble_route()`, and label the output as `사업 객체` rather than `노선 중심선`.
- [ ] Buffer the complete multi-part geometry and record project type plus actual source layers in the manifest.
- [ ] Verify the AOI covers every source object and the generated GPKG/SHP/QGZ remain valid.

### Task 4: Export per-layer preview Shapefiles

**Files:**
- Modify: `src/routegis/app/project_service.py` or add a focused preview module under `src/routegis/cad/`
- Test: `tests/integration/test_pipeline.py`

- [ ] Add a core preview API that reads every analyzed CAD layer and exports one temporary Shapefile per layer with individual entities and source-layer attributes.
- [ ] Keep preview output outside the final project directory and avoid route topology validation.
- [ ] Verify each Shapefile exists, has the expected feature count and CRS, and can be reopened.

### Task 5: Add the QGIS project-type and preview workflow

**Files:**
- Modify: `plugin/wizard.py`
- Test: `tests/unit/test_plugin_wizard.py` or installed-QGIS smoke script

- [ ] After DXF analysis, ask `대상 사업이 선형 사업이십니까?` with `예` and `아니요`.
- [ ] In non-linear mode, disable centerline selection requirements and explain that all supported line objects will define the buffer.
- [ ] In linear mode, create preview Shapefiles, add them to a dedicated `RouteGIS 선형 후보 미리보기` group in the current `QgsProject`, zoom to their combined extent, and keep the Wizard open for row selection.
- [ ] Remove old preview layers and temporary files when re-analyzing or closing the Wizard.
- [ ] Ensure final build passes the selected project type and only requires selected centerline rows in linear mode.

### Task 6: Documentation and installed verification

**Files:**
- Modify: `README.md`
- Rebuild: `dist/routegis_autobuilder-0.1.0-py3-none-any.whl`
- Install: QGIS Python user site and QGIS profile plugin directory

- [ ] Document the two project modes, searchable VWorld identifiers, and temporary-preview lifecycle.
- [ ] Run LSP diagnostics on every changed Python file.
- [ ] Run the complete pytest suite and Python 3.9 compile check.
- [ ] Build and reinstall the wheel without overwriting the installed `.env` credential file.
- [ ] Copy plugin files while excluding `.env`.
- [ ] In installed QGIS 3.34, search by a real VWorld `data_id`, exercise both popup responses, inspect per-layer preview Shapefiles, select a linear centerline, and build a branched non-linear project.

This workspace is not a Git repository, so the plan intentionally omits commit steps.
