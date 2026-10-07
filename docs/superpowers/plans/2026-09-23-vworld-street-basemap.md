# VWorld Street Basemap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add TMS for Korea's VWorld Street as the visible, absolute bottom root layer in every newly generated QGIS project.

**Architecture:** Keep the basemap inside the QGIS project writer rather than the VWorld feature catalog because it is a presentation-only XYZ raster and is never downloaded into the project GeoPackage. Define one canonical datasource URI and emit equivalent layers in both the headless QGS XML path and the native PyQGIS path.

**Tech Stack:** Python 3.9+, ElementTree QGS XML, PyQGIS 3.34+, pytest

---

### Task 1: Headless QGZ regression contract

**Files:**
- Modify: `tests/integration/test_pipeline.py:58`

- [ ] **Step 1: Extend the golden pipeline test**

Parse the root `layer-tree-group` and assert its last direct child is a `layer-tree-layer` with `name="VWorld Street"`, `providerKey="wms"`, and `checked="Qt::Checked"`. Find the `maplayer` with the same ID and assert `type="raster"`, `layername` is `VWorld Street`, `datasource` equals the canonical URI, `srs/spatialrefsys/authid` is `EPSG:3857`, and `provider` has text `wms`, `encoding="UTF-8"`, and `mapUnitsMm="0"`. Assert it has no `renderer-v2`, and the last `layerorder/layer` references the same ID.

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `& ".venv\Scripts\python.exe" -m pytest tests/integration/test_pipeline.py::test_golden_pipeline_preserves_successes_and_reports_failures -q`

Expected: FAIL because the generated QGS has no `VWorld Street` layer.

### Task 2: Native and headless project writer parity

**Files:**
- Modify: `src/routegis/qgis/project.py:15-255`

- [ ] **Step 1: Define the canonical layer constants**

Add constants for the layer name and exact QGIS datasource URI:

```python
VWORLD_STREET_NAME = "VWorld Street"
VWORLD_STREET_URI = (
    "url=https://xdworld.vworld.kr/2d/Base/service/{z}/{x}/{y}.png"
    "&zmax=18&zmin=0&type=xyz&tilePixelRatio=1"
)
```

- [ ] **Step 2: Emit the headless root layer**

After all groups and vector/source nodes exist, compute `identifier = _layer_id(VWORLD_STREET_NAME)` and append this direct root child:

```python
ET.SubElement(
    tree_root,
    "layer-tree-layer",
    {
        "id": identifier,
        "name": VWORLD_STREET_NAME,
        "source": VWORLD_STREET_URI,
        "providerKey": "wms",
        "checked": "Qt::Checked",
    },
)
```

Append `maplayer type="raster"` to `projectlayers` with the same ID, `datasource` set to `VWORLD_STREET_URI`, `layername` set to `VWORLD_STREET_NAME`, and `srs` populated by `_srs_element(srs, "EPSG:3857")`. Add `provider` with attributes `encoding="UTF-8"`, `mapUnitsMm="0"` and text `wms`; do not call the vector-only `_renderer()`. Append the ID as the last `layerorder/layer`. ElementTree must perform the XML `&amp;` escaping automatically.

- [ ] **Step 3: Emit the native root layer**

Import `QgsRasterLayer`, instantiate it from the canonical URI, reject an invalid layer with `ExportError`, register it using `project.addMapLayer(layer, False)`, and append it using `root.addLayer(layer)` after all grouped layers. Keep the returned node checked with `setItemVisibilityChecked(True)` so native output matches `Qt::Checked` in the headless XML.

- [ ] **Step 4: Run file diagnostics**

Run LSP diagnostics on `src/routegis/qgis/project.py` and `tests/integration/test_pipeline.py` and resolve every new error.

- [ ] **Step 5: Run the focused test and verify it passes**

Run the same focused pytest command from Task 1.

Expected: PASS.

### Task 3: Regression and real-surface validation

**Files:**
- Verify: `src/routegis/qgis/project.py`
- Verify: `tests/integration/test_pipeline.py`

- [ ] **Step 1: Run the full automated suite**

Run: `& ".venv\Scripts\python.exe" -m pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Generate a real fixture project through the CLI**

Run `routegis build` with `tests/fixtures/sample_route.dxf` and `tests/fixtures/vworld_mock.json` into a temporary output folder. Confirm command exit code 0.

- [ ] **Step 3: Open the generated QGZ with installed PyQGIS**

Use `C:\Program Files\QGIS 3.34.3\bin\python-qgis.bat` to read the QGZ. Assert the project root's last child is `VWorld Street`, its raster layer is valid and uses provider `wms`, and all other root groups precede it.

- [ ] **Step 4: Confirm no regression in project artifacts**

Confirm the QGZ still contains exactly one QGS member and the existing GeoPackage, source drawing, styles, reports, logs, manifest, and Shapefile outputs remain present.
