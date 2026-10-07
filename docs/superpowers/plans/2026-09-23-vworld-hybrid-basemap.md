# VWorld Hybrid Basemap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add TMS for Korea's two-layer VWorld Hybrid composite immediately below the existing VWorld Street layer in every generated QGIS project.

**Architecture:** Extend the existing project-writer-only basemap implementation. Keep one ordered tuple of Hybrid child name/URI pairs and emit the same root group, child order, visibility, raster metadata, and QGIS 3.34.3 layerorder in both headless XML and native PyQGIS paths.

**Tech Stack:** Python 3.9+, ElementTree QGS XML, PyQGIS 3.34.3, pytest

---

### Task 1: Hybrid QGS regression contract

**Files:**
- Modify: `tests/integration/test_pipeline.py:69-96`

- [ ] **Step 1: Extend the golden project assertions**

Change the Street node expectation from the last root child to the second-last root child. Assert the last root child is a checked, expanded `VWorld Hybrid` group whose children are exactly `VWorld Hybrid Label` then `VWorld Hybrid`, both checked and using `providerKey="wms"`.

For both children, find the matching raster `maplayer` by ID and verify the exact URI, `EPSG:3857`, provider text `wms`, provider attributes `encoding="UTF-8"` and `mapUnitsMm="0"`, and absence of `renderer-v2`. Assert the last three QGS `layerorder` IDs are Street, Label, and Satellite in that measured QGIS 3.34.3 order.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `& ".venv\Scripts\python.exe" -m pytest tests/integration/test_pipeline.py::test_golden_pipeline_preserves_successes_and_reports_failures -q`

Expected: FAIL because the generated QGS root has no `VWorld Hybrid` group.

### Task 2: Native and headless Hybrid parity

**Files:**
- Modify: `src/routegis/qgis/project.py:25-288`

- [ ] **Step 1: Define the canonical ordered Hybrid configuration**

Add a group-name constant and an ordered tuple containing Label first and Satellite second:

```python
VWORLD_HYBRID_GROUP_NAME = "VWorld Hybrid"
VWORLD_HYBRID_LAYERS = (
    (
        "VWorld Hybrid Label",
        "url=https://xdworld.vworld.kr/2d/Hybrid/service/{z}/{x}/{y}.png"
        "&zmax=18&zmin=0&type=xyz&tilePixelRatio=1",
    ),
    (
        "VWorld Hybrid",
        "url=https://xdworld.vworld.kr/2d/Satellite/service/{z}/{x}/{y}.jpeg"
        "&zmax=18&zmin=0&type=xyz&tilePixelRatio=1",
    ),
)
```

- [ ] **Step 2: Emit the headless Hybrid group**

After the existing Street layer, append a direct root `layer-tree-group` named `VWorld Hybrid` with `checked="Qt::Checked"` and `expanded="1"`. Iterate the ordered tuple to add child `layer-tree-layer` nodes and matching `_layer_id(name)` raster `maplayer` elements. Each raster provider has text `wms` and attributes `encoding="UTF-8"`, `mapUnitsMm="0"`. Append each child ID to `layerorder`; the resulting suffix must be Street, Label, Satellite.

- [ ] **Step 3: Emit the native Hybrid group**

After adding Street, call `root.addGroup(VWORLD_HYBRID_GROUP_NAME)`, mark it checked and expanded, then iterate the same ordered tuple. Construct each `QgsRasterLayer(uri, name, "wms")`, raise `ExportError` with the layer name if invalid, add it to the project without automatic legend insertion, and add a checked child node to the group.

- [ ] **Step 4: Run diagnostics and verify GREEN**

Run LSP diagnostics on both changed Python files, then rerun the focused pytest command from Task 1.

Expected: no Python diagnostics and focused test PASS.

### Task 3: Regression and QGIS surface validation

**Files:**
- Verify: `src/routegis/qgis/project.py`
- Verify: `tests/integration/test_pipeline.py`

- [ ] **Step 1: Run the full suite**

Run: `& ".venv\Scripts\python.exe" -m pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Generate a headless CLI project**

Build the golden DXF with the fixture provider using `.venv\Scripts\routegis.exe`, where PyQGIS is unavailable and the headless writer is selected. Confirm exit code 0 and expected project artifacts.

- [ ] **Step 3: Generate and read a native PyQGIS project**

Use `C:\Program Files\QGIS 3.34.3\bin\python-qgis.bat` with an initialized `QgsApplication` to force the native writer and build a second project through `ProjectService`, then read both native and headless QGZ files.

- [ ] **Step 4: Assert the real QGIS layer surface**

For both projects, assert the root suffix is `VWorld Street`, `VWorld Hybrid`; the Hybrid group is expanded, visible, and contains visible `VWorld Hybrid Label`, `VWorld Hybrid` children in that order; both child layers are valid `wms` rasters in `EPSG:3857`; and their normalized sources contain the official Hybrid PNG and Satellite JPEG URLs.
