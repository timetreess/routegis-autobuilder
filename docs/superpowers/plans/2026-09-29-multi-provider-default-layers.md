# Multi-Provider Default Layers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship RouteGIS `0.2.0` with seven new default spatial layers sourced from VWorld, the National Institute of Ecology WFS, and OSM Overpass, including long-route tiling, complete provenance/reporting, QGIS presentation, and an updated local QGIS 3.34.3 installation.

**Architecture:** Keep the existing `SpatialDataProvider.fetch_layer(definition, aoi)` boundary. Add a provider-neutral catalog and AOI tile planner, then implement `EcologyClient` and `OverpassClient` alongside the migrated `VWorldClient`; `ProjectService` lazily routes by `LayerDefinition.provider`, joins immutable source metadata into reports, and exports only atomically successful layers. QGIS headless/native writers share one group/style mapping and the Wizard remains a thin surface over the same catalog and provider contracts.

**Tech Stack:** Python 3.9, httpx 0.27, defusedxml, Shapely 2, pyproj, PyYAML, PyQt/QGIS 3.34.3, pytest

**Source of truth:** `docs/superpowers/specs/2026-09-29-default-spatial-data-providers-design.md`

**Workspace note:** Existing uncommitted security/atomic-publish changes and the `0.1.1` version bump are intentional prerequisites. Do not revert them. No Git commit steps are included because the user did not request commits.

---

## File Structure

### New modules

- `src/routegis/providers/catalog.py`: provider-neutral `LayerCatalog`, query matching, VWorld taxonomy bridge.
- `src/routegis/gis/tiling.py`: output-CRS tile planning, child splitting, and request-budget calculation.
- `src/routegis/providers/ecology/__init__.py`, `client.py`: Ecology WFS request, secure GML2 parsing, saturation splitting.
- `src/routegis/providers/overpass/__init__.py`, `client.py`: exact Overpass queries, OSM geometry parsing, relation assembly.
- `tests/unit/test_config.py`: strict settings/catalog schema tests.
- `tests/unit/test_tiling.py`: tile grid, split, CRS and budget tests.
- `tests/integration/test_ecology.py`: Ecology HTTP/GML contract tests.
- `tests/integration/test_overpass.py`: Overpass query/parser/retry contract tests.
- `tests/fixtures/ecology_wfs_response.xml`, `overpass_power.json`, `overpass_river.json`, `spatial_mock.json`: network-free protocol and pipeline fixtures.
- `src/routegis/resources/styles/ecology.qml`, `heritage.qml`, `river_line.qml`, `river_area.qml`, `power_line.qml`, `power_support.qml`: new native QGIS styles.

### Deleted or replaced files

- Delete `src/routegis/providers/vworld/catalog.py` after moving it to `providers/catalog.py`; do not add a compatibility alias.
- Replace `tests/fixtures/vworld_mock.json` with `tests/fixtures/spatial_mock.json`.

---

### Task 1: Shared model, settings, and credential contracts

**Files:**
- Create: `tests/unit/test_config.py`
- Modify: `tests/unit/test_environment.py`
- Modify: `src/routegis/models.py`
- Modify: `src/routegis/config.py`
- Modify: `src/routegis/environment.py`
- Modify: `src/routegis/resources/config.yaml`

- [ ] **Step 1: Add failing model/config tests.** Assert that `LayerDefinition` requires `publisher`, `dataset_id`, `license_name`, and `license_url`; allows optional `aliases`; `DownloadResult` exposes the five tile-stat fields with zero/`None` defaults; `load_layer_catalog` accepts `overpass` and rejects missing metadata. Assert every exact approved setting value: six `download_*` fields; retained `vworld_timeout`, `vworld_retry`, `vworld_backoff`, and `vworld_page_size`; two new VWorld tile-size fields; four Ecology fields; and four OSM fields.
- [ ] **Step 2: Add failing credential tests.** Mirror the existing VWorld environment tests for `DATA_GO_KR_SERVICE_KEY`: process environment wins, then dotenv files in argument order, then `""`.
- [ ] **Step 3: Run the red tests.**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests\unit\test_config.py tests\unit\test_environment.py -v
```

Expected: failures for missing fields/settings/helper.

- [ ] **Step 4: Extend the dataclasses.** Keep `LayerDefinition` frozen; add the four required source fields and `aliases: tuple[str, ...] = ()`. Add to `DownloadResult`:

```python
initial_tile_count: int = 0
request_count: int = 0
successful_tile_count: int = 0
failed_tile_count: int = 0
failed_tile_bbox: tuple[float, float, float, float] | None = None
```

- [ ] **Step 5: Keep `AppSettings` flat and surgical.** Remove only `vworld_max_pages`; retain `vworld_timeout`, `vworld_retry`, `vworld_backoff`, and `vworld_page_size`. Add the exact prefixed fields for six `download_*` values, `vworld_land_ownership_tile_size_m`, `vworld_default_tile_size_m`, four Ecology values (`tile_size_m`, `timeout`, `retry`, `page_size`), and four OSM values (`endpoint`, `tile_size_m`, `timeout`, `retry`). Preserve existing CAD/export/QGIS fields.
- [ ] **Step 6: Update `config.yaml` with the approved values.** Use the exact endpoint, timeout, retry, page/tile sizes, response cap, depth, and request-budget values from the spec; do not expose them in the Wizard.
- [ ] **Step 7: Implement `load_data_go_kr_service_key`.** Match `load_vworld_api_key` behavior without logging paths or values.
- [ ] **Step 8: Run targeted tests and diagnostics.**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests\unit\test_config.py tests\unit\test_environment.py -v
```

Expected: PASS.

---

### Task 2: Provider-neutral catalog and resource migration

**Files:**
- Create: `src/routegis/providers/catalog.py`
- Modify: `src/routegis/providers/__init__.py`
- Modify: `src/routegis/providers/vworld/client.py`
- Modify: `src/routegis/providers/vworld/__init__.py`
- Modify: `src/routegis/app/project_service.py`
- Modify: `src/routegis/resources/layer_catalog.yaml`
- Modify: `src/routegis/resources/presets/basic_route.yaml`
- Modify: `.env.example`
- Modify: `plugin/.env.example`
- Modify: `tests/unit/test_wizard_search.py`
- Modify: `tests/integration/test_pipeline.py`
- Delete: `src/routegis/providers/vworld/catalog.py`

- [ ] **Step 1: Update failing catalog tests before moving code.** Rename imports/usages to `LayerCatalog`; add the four metadata fields to every direct `LayerDefinition` in unit and pipeline tests before those tests run; test search by provider, publisher, dataset ID, aliases, RouteGIS ID, display name, VWorld taxonomy alias, and raw data ID; assert only the new seven IDs are in `basic_route`.
- [ ] **Step 2: Run the catalog tests and confirm failure.**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests\unit\test_wizard_search.py -v
```

- [ ] **Step 3: Move `VWorldCatalog` to `LayerCatalog` and migrate every importer before deletion.** Preserve `get`, `select`, `all`, `snapshot`, `merge_live`, `taxonomy_for`, `matches`, and `sort_key`. Update `ProjectService`, provider package exports, VWorld package exports, Wizard/tests, and any remaining callers to import `routegis.providers.catalog.LayerCatalog`. Apply `VWorldTaxonomy` only when `definition.provider == "vworld"`; fixed Ecology/OSM aliases come from `LayerDefinition.aliases`.
- [ ] **Step 4: Keep live VWorld definitions complete at creation.** Update `VWorldClient.discover_layers()` to set the required VWorld publisher/license metadata and `dataset_id=data_id`; do not add compatibility defaults in `LayerDefinition` or `LayerCatalog`.
- [ ] **Step 5: Migrate all resource definitions atomically.** Keep the existing seven entries and categories, add their required VWorld metadata, then add the approved seven new entries with exact service types, endpoints, IDs, CRSs, geometry types, categories, aliases, publishers, dataset IDs, and licenses. Set `river_area.response_crs` to `EPSG:4326` as approved.
- [ ] **Step 6: Replace the default profile.** `basic_route.yaml` contains only `ecology_nature`, `land_ownership`, `heritage_protection`, `power_line`, `power_support`, `river_centerline`, and `river_area` in that order.
- [ ] **Step 7: Add `DATA_GO_KR_SERVICE_KEY=` to both example env files.** Do not edit root `.env` or deployed plugin `.env`.
- [ ] **Step 8: Prove no caller imports the old path, then remove the old catalog module and re-run tests.**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests\unit\test_wizard_search.py tests\unit\test_vworld_taxonomy.py -v
```

Expected: PASS with no import of `routegis.providers.vworld.catalog`.

---

### Task 3: Offline pipeline fixture migration

**Files:**
- Create: `tests/fixtures/spatial_mock.json`
- Modify: `tests/integration/test_pipeline.py`
- Modify: `README.md`
- Delete: `tests/fixtures/vworld_mock.json`

- [ ] **Step 1: Change fixture references first and confirm they fail.** Replace every `vworld_mock.json` path with `spatial_mock.json` while keeping existing cadastral-success/building-failure assertions.
- [ ] **Step 2: Build the single-CRS fixture.** Use top-level `EPSG:5186`; retain current `cadastral` success and `building` error; add all seven successful layers with the exact geometry types and approved sample coordinates. Add minimal source properties (`eczm_grad`, VWorld IDs, OSM `type/id/tags`) without real credentials.
- [ ] **Step 3: Document the renamed fixture in the README CLI example.**
- [ ] **Step 4: Run the existing pipeline tests.**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests\integration\test_pipeline.py -v
```

Expected: the original golden and rollback behavior passes before group/report assertions are changed later.

---

### Task 4: Provider-neutral AOI tiling and request budgets

**Files:**
- Create: `src/routegis/gis/tiling.py`
- Create: `tests/unit/test_tiling.py`
- Modify: `src/routegis/gis/__init__.py`

- [ ] **Step 1: Write failing pure tests.** Cover square grids in metre output CRS, AOI-intersection filtering, deterministic row/column order, request-CRS bbox transformation, longest-axis bisection, depth 32, 50 m minimum side, and budget boundary cases for `min(maximum, max(minimum, initial_count * per_tile))`.
- [ ] **Step 2: Run the red test.**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests\unit\test_tiling.py -v
```

- [ ] **Step 3: Implement focused value objects.**

```python
@dataclass(frozen=True)
class AoiTile:
    output_bounds: tuple[float, float, float, float]
    request_bounds: tuple[float, float, float, float]
    depth: int = 0

class AoiTilePlanner:
    def initial_tiles(self) -> list[AoiTile]: ...
    def split(self, tile: AoiTile) -> list[AoiTile]: ...

def compute_request_budget(initial_count: int, minimum: int, per_tile: int, maximum: int) -> int: ...
```

The planner receives the original output-CRS AOI, request CRS, tile size, split depth, and minimum size. It never performs HTTP or stores provider state.
- [ ] **Step 4: Run tests and LSP diagnostics.** Expected: PASS and no changed-file diagnostics.

---

### Task 5: Migrate VWorld to dynamic tiling without weakening security

**Files:**
- Modify: `tests/integration/test_vworld.py`
- Modify: `src/routegis/providers/vworld/client.py`

- [ ] **Step 1: Update every VWorld test definition with required metadata.** Preserve existing categories and service behavior.
- [ ] **Step 2: Add failing VWorld tiling/stat tests.** Cover more than 100 initial land-ownership tiles, 5 km vs 10 km sizes, request budget including retry attempts, saturated parent replacement by children, Data API per-tile pagination, successful counters, failed bbox in output CRS, atomic failure after one terminal tile error, same-ID content conflicts, minimum size/depth stops, and unchanged 64 MiB/DTD/redirect/redaction protections.
- [ ] **Step 3: Run the VWorld suite and confirm the new cases fail.**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests\integration\test_vworld.py -v
```

- [ ] **Step 4: Remove `max_pages` and integrate `AoiTilePlanner`.** WFS requests use 1,000-feature leaves and discard saturated parent payloads before splitting. Data API requests page within each planned tile until a short page; repeated-page detection remains.
- [ ] **Step 5: Count every real transport attempt.** Call the budget counter immediately before each `httpx` stream attempt, including retries. Preserve provider-specific `TRANSIENT_STATUS`, exponential backoff, redirect refusal, content-length and streamed-size guards, XML DTD rejection, response-CRS validation, geometry repair, and credential redaction.
- [ ] **Step 6: Return failed `DownloadResult` from provider-local failures.** Once tile stats exist, catch the client’s own `ProviderError` at the outer `fetch_layer` boundary and return a redacted failed result carrying counters and output-CRS failed bbox; constructor/initialization failures still propagate to `ProjectService`.
- [ ] **Step 7: Run VWorld and tiling tests.**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests\unit\test_tiling.py tests\integration\test_vworld.py -v
```

Expected: PASS; all prior security regression tests remain green.

---

### Task 6: Implement the Ecology WFS provider

**Files:**
- Create: `src/routegis/providers/ecology/__init__.py`
- Create: `src/routegis/providers/ecology/client.py`
- Create: `tests/fixtures/ecology_wfs_response.xml`
- Create: `tests/integration/test_ecology.py`

- [ ] **Step 1: Create the official GML2 fixture.** Transcribe the documented WFS 1.0.0 `FeatureCollection`/`featureMember`/`tbl_opn_eczm fid` envelope, add an interior ring for coverage, preserve EPSG:5186 `gml:coordinates`, and omit keys/full request URLs.
- [ ] **Step 2: Write failing request/parser tests.** Assert exact endpoint and `serviceKey`, `typeName`, bbox order, `maxFeatures=500`; namespace-independent parsing; `fid` IDs; exterior/interior ring parsing; auto-closing open rings; all official scalar fields; CRS mismatch/unknown CRS; missing geometry; malformed exterior/interior rings, degenerate rings, and polygons that remain unrecoverable; DTD, redirect, response-size, retry, redaction, empty response, and duplicate-ID conflict behavior. Every malformed/unrecoverable geometry case must return a failed `DownloadResult`, produce no partial layer, and populate failure statistics.
- [ ] **Step 3: Add saturation and atomicity tests.** Generate 500 feature members inline, confirm the parent payload is discarded, child tiles are used, every HTTP attempt consumes budget, and any terminal child failure returns one failed layer with no partial output.
- [ ] **Step 4: Run red tests.**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests\integration\test_ecology.py -v
```

- [ ] **Step 5: Implement `EcologyClient`.** Mirror the hardened VWorld transport pattern with Ecology-specific errors and key redaction; parse only XML/GML2 using `defusedxml` with DTD forbidden; find elements by namespace URI/local name, not prefixes; use the common 10 km planner and 500-feature saturation split; transform complete intersecting Geometry back to output CRS without clipping.
- [ ] **Step 6: Run Ecology, tiling, and environment tests.** Expected: PASS.

---

### Task 7: Implement the Overpass provider

**Files:**
- Create: `src/routegis/providers/overpass/__init__.py`
- Create: `src/routegis/providers/overpass/client.py`
- Create: `tests/fixtures/overpass_power.json`
- Create: `tests/fixtures/overpass_river.json`
- Create: `tests/integration/test_overpass.py`

- [ ] **Step 1: Create small hand-authored fixtures.** Power includes one `power=line`, its tower and pole nodes, an independent pole, and a `minor_line`. River includes river/stream centerlines, natural-water river way/relation, legacy riverbank, outer/inner members, and duplicate member ways.
- [ ] **Step 2: Write failing exact-query tests.** Compare normalized request bodies to the approved QL; assert POST, bbox order `south,west,north,east`, timeout 180, and configurable endpoint.
- [ ] **Step 3: Write failing parser tests.** Cover node points; way lines/polygons; `node(w.lines)` membership; exclusion of minor lines/independent poles; relation outer/inner endpoint joining; ring closure; uncontained inner/missing member/unrepairable polygon failures; relation-member suppression; ID and normalized-content deduplication; complete geometry preserved when it intersects AOI.
- [ ] **Step 4: Write failing transport/stat tests.** Assert retries only for 429/502/503/504 and transport timeout, 500 is terminal, max three attempts, 64 MiB guard, 25 km sequential tiles, budget consumption, atomic failure, empty success, and ODbL metadata availability through the catalog definition.
- [ ] **Step 5: Run red tests.**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests\integration\test_overpass.py -v
```

- [ ] **Step 6: Implement one focused client module.** Keep query builders and parser helpers pure within `client.py` to match the existing provider style; do not add speculative packages. Use `AoiTilePlanner`, provider-specific retry constants, secure streamed response limits, and `SpatialLayer(source="osm")`.
- [ ] **Step 7: Run Overpass and tiling tests.** Expected: PASS.

---

### Task 8: Multi-provider orchestration, exports, manifest, and report

**Files:**
- Modify: `src/routegis/providers/__init__.py`
- Modify: `src/routegis/app/project_service.py`
- Modify: `src/routegis/gis/export.py`
- Modify: `src/routegis/gis/shapefile.py`
- Modify: `src/routegis/report.py`
- Modify: `src/routegis/cli.py`
- Modify: `tests/integration/test_pipeline.py`

- [ ] **Step 1: Add failing pipeline assertions.** Run all seven new fixture layers and assert GeoPackage tables, category-specific Shapefile paths, atomic failed-layer exclusion, `COMPLETED_WITH_WARNINGS`, and exact manifest/report fields (`id`, `name`, source/license fields, tile counters, output-CRS failed bbox). Assert both secrets are absent from manifest/report/log. Define and assert one artifact attribution contract: a `routegis_layer_metadata` GeoPackage table, `<layer>.metadata.json` beside each Shapefile, and later QGIS layer custom properties, all carrying `provider`, `publisher`, `dataset_id`, copyright/attribution text, `license_name`, and `license_url`. Assert JSON and HTML reports contain the exact geometry-policy notice: `AOI 교차 객체를 원형 보존하며 AOI 밖 Geometry가 함께 보일 수 있습니다.`
- [ ] **Step 2: Add fake-factory orchestration tests.** Verify selected-layer order, lazy creation and one-client-per-provider reuse, injected-provider override for every layer, unknown provider, missing credential, and constructor failure as one failed result while later layers continue, plus `finally` closure of every owned closeable client. Assert injected providers are not closed or bypassed.
- [ ] **Step 3: Add CLI tests or a bounded driver assertion.** A fixture build needs no keys; a non-fixture build loads VWorld and data.go.kr keys independently from environment/dotenv.
- [ ] **Step 4: Run red pipeline tests.**

```powershell
& ".venv\Scripts\python.exe" -m pytest tests\integration\test_pipeline.py -v
```

- [ ] **Step 5: Replace the single factory with lazy provider routing.** Cache owned clients by `definition.provider`; use VWorld key/domain only for `vworld`, the Ecology key only for `ecology`, no credential for `osm`; injected providers still serve every selected layer. Catch initialization/unknown-provider errors per layer and close every owned closeable client in `finally`.
- [ ] **Step 6: Redact both credentials.** `_SecretFilter`, `_logger`, and `_redact` accept the two non-empty secrets and never log URLs with query strings.
- [ ] **Step 7: Add new Shapefile directories without changing legacy categories.** Extend `PROJECT_DIRECTORIES` with `shp/land`, `shp/ecology`, `shp/heritage`, `shp/water`, and `shp/power`; keep `shp/cadastral` and `shp/facility`.
- [ ] **Step 8: Join results to definitions for reporting and artifact attribution.** Keep source/license metadata in `LayerDefinition`, not `DownloadResult`; emit approved field names in manifest and JSON report; render readable HTML source/license/tile columns and OSM attribution; use existing `error` as last error. Write the same metadata into `routegis_layer_metadata` in the GeoPackage and deterministic `<layer>.metadata.json` Shapefile sidecars without credentials or request URLs. Add `geometry_policy: "AOI 교차 객체를 원형 보존하며 AOI 밖 Geometry가 함께 보일 수 있습니다."` to the JSON report and render the same sentence visibly in HTML.
- [ ] **Step 9: Extend the CLI without exposing keys.** Load `DATA_GO_KR_SERVICE_KEY` from root `.env` when no fixture, pass it to `ProjectService.build`, and keep `--domain` VWorld-only. Do not print credentials.
- [ ] **Step 10: Run pipeline, provider, and CLI fixture tests.** Expected: PASS.

---

### Task 9: Wizard key, catalog search, and default-state UI

**Files:**
- Modify: `plugin/wizard.py`
- Modify: `tests/unit/test_wizard_search.py`

- [ ] **Step 1: Extend unit-testable search/default assertions.** Verify provider/publisher/dataset/alias search and exact seven-ID profile behavior independently of PyQt.
- [ ] **Step 2: Add a second Password-echo input.** Label it for the public-data-portal service key, leave domain explicitly VWorld-only, and preserve the existing VWorld live-list button.
- [ ] **Step 3: Expand tooltip content.** Show provider, RouteGIS ID, request `data_id`, external `dataset_id`, publisher, license name, and license URL; retain VWorld taxonomy category/name where present.
- [ ] **Step 4: Preserve checked state by RouteGIS ID.** Fixed Ecology/OSM entries remain during VWorld live refresh; filtering or refresh never loses checked IDs.
- [ ] **Step 5: Apply and test Ecology credential precedence explicitly.** Resolve `load_data_go_kr_service_key(PLUGIN_DOTENV) or temporary_input`, so process environment wins, then installed plugin `.env`, then the temporary Wizard field. Pass only the resolved value to `ProjectService.build`, clear both key fields in `finally`, and verify neither key nor the temporary value is persisted to `QSettings`.
- [ ] **Step 6: Run wizard search tests and changed-file diagnostics.** Expected: PASS/clean.

---

### Task 10: QGIS groups, basemaps, and styles

**Files:**
- Modify: `src/routegis/app/project_service.py`
- Modify: `src/routegis/qgis/project.py`
- Modify: `src/routegis/resources/styles/cadastral.qml`
- Create: `src/routegis/resources/styles/ecology.qml`
- Create: `src/routegis/resources/styles/heritage.qml`
- Create: `src/routegis/resources/styles/river_line.qml`
- Create: `src/routegis/resources/styles/river_area.qml`
- Create: `src/routegis/resources/styles/power_line.qml`
- Create: `src/routegis/resources/styles/power_support.qml`
- Modify: `tests/integration/test_pipeline.py`

- [ ] **Step 1: Change QGZ assertions first.** Assert the exact 11-group order; map `land` and legacy `cadastral` to `02_토지`; map Ecology/heritage/water/power/admin/landuse/facility/source to approved groups; assert Street and Hybrid are children of `00_기본도`, not root children. For every successful provider layer only, assert both headless and native QGIS paths write `routegis/provider`, `routegis/publisher`, `routegis/dataset_id`, `routegis/attribution`, `routegis/license_name`, and `routegis/license_url` custom properties; OSM values must contain `© OpenStreetMap contributors`, the copyright URL, and ODbL 1.0. Route, AOI, basemap, and source layers have no `LayerDefinition` and are explicitly excluded from this assertion.
- [ ] **Step 2: Add headless renderer assertions.** Check exact colors, alpha, line widths, and point size for each new layer ID.
- [ ] **Step 3: Implement shared group/style and metadata resolvers.** In `ProjectService`, build an immutable metadata mapping keyed by successful provider layer ID from the selected `LayerDefinition` objects and pass it explicitly to `write_qgis_project`. Both `_headless_qgs` and `_native_project` call the same ID-aware style-name resolver, category-to-group mapping, and source/license custom-property mapping. Existing unknown/facility entries fall back to `09_시설물`; non-provider route/AOI/basemap/source layers receive no provider metadata.
- [ ] **Step 4: Create the six QML files and update `cadastral.qml`.** Match approved colors and widths exactly; keep route/buffer/landuse styles otherwise unchanged.
- [ ] **Step 5: Move both VWorld basemap paths under `00_기본도`.** Preserve layer order, visibility, providers, URIs, and default extent.
- [ ] **Step 6: Run pipeline tests and inspect the generated QGZ XML.** Expected: PASS and matching headless tree/style XML.

---

### Task 11: Version, documentation, and full automated verification

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/routegis/__init__.py`
- Modify: `plugin/metadata.txt`
- Modify: `README.md`

- [ ] **Step 1: Update user documentation.** Describe two credentials, seven defaults, source/license attribution, Overpass/ODbL, category output paths, long-route tiling, atomic layer failure, and the renamed fixture. Remove the statement that large-AOI tiling is out of scope.
- [ ] **Step 2: Bump the three version sources to `0.2.0`.** Ensure no stale `0.1.1` remains in package/plugin metadata or README release examples.
- [ ] **Step 3: Run diagnostics on every changed Python file.** Resolve only errors introduced by this work; report unrelated pre-existing diagnostics.
- [ ] **Step 4: Run the full suite once.**

```powershell
& ".venv\Scripts\python.exe" -m pytest
```

Expected: all tests pass.

- [ ] **Step 5: Compile and build.**

```powershell
& ".venv\Scripts\python.exe" -m compileall -q src plugin
& ".venv\Scripts\python.exe" -m build
& ".venv\Scripts\python.exe" -m pip check
```

Expected: exit 0 and `dist/routegis_autobuilder-0.2.0-py3-none-any.whl` exists.

---

### Task 12: Surface QA, QGIS 3.34.3 install, and manual acceptance

**Files/Surfaces:**
- Create: `tests/manual/qgis_smoke.py`
- Run: CLI fixture build
- Install: QGIS Python 3.9 user site and `routegis_autobuilder` plugin folder
- Smoke: actual QGIS 3.34.3 Python, plugin factory/Wizard/QGZ

- [ ] **Step 1: Drive the CLI happy path through the shipped fixture.**

```powershell
& ".venv\Scripts\routegis.exe" build "tests\fixtures\sample_route.dxf" `
  --project-name "RouteGIS 0.2.0 QA" `
  --output "output-qgis" `
  --route-layer CENTER `
  --source-crs EPSG:5186 `
  --output-crs EPSG:5186 `
  --buffer 1000 `
  --layers ecology_nature,land_ownership,heritage_protection,power_line,power_support,river_centerline,river_area `
  --fixture "tests\fixtures\spatial_mock.json"
```

Observe seven successful output layers, correct directories, QGZ groups/styles, provenance/report rows, and no credential leakage.

- [ ] **Step 2: Drive CLI bad input and help.** Run `routegis --help`, `routegis build --help`, and one missing-fixture-layer build; confirm bounded Korean error output and no traceback/secret.
- [ ] **Step 3: Run bounded live Overpass smoke.** Use the sample AOI or a smaller test AOI; verify power and river outputs plus ODbL attribution. Record provider availability failures separately from code failures.
- [ ] **Step 4: Run live VWorld smoke when the existing key/domain are available.** Fetch `land_ownership` and `heritage_protection`; never print the key. The long-route acceptance has explicit prerequisites `$env:ROUTEGIS_LONG_ROUTE_DXF` and `$env:ROUTEGIS_VWORLD_DOMAIN`; fail this manual gate as unavailable rather than guessing a path when either is absent. When present, run:

```powershell
if (-not $env:ROUTEGIS_LONG_ROUTE_DXF -or -not $env:ROUTEGIS_VWORLD_DOMAIN) { throw "Set ROUTEGIS_LONG_ROUTE_DXF and ROUTEGIS_VWORLD_DOMAIN" }
& ".venv\Scripts\routegis.exe" build $env:ROUTEGIS_LONG_ROUTE_DXF `
  --project-name "RouteGIS 0.2.0 Long Route QA" `
  --output "output-qgis-long-route" `
  --route-layer CENTER `
  --source-crs EPSG:5186 `
  --output-crs EPSG:5186 `
  --buffer 1000 `
  --layers land_ownership `
  --domain $env:ROUTEGIS_VWORLD_DOMAIN
```

Confirm the approximately 564.5 km input can exceed 100 real requests, completes without partial output, and reports all tile counters.
- [ ] **Step 5: Run live Ecology smoke only if `DATA_GO_KR_SERVICE_KEY` exists.** Confirm one EPSG:5186 request and parsed feature; otherwise explicitly record that fixture verification is the strongest available evidence.
- [ ] **Step 6: Install pure-Python dependencies and the wheel into QGIS Python while proving `.env` preservation.** Use the exact runtime and preserve the deployed secret file byte-for-byte:

```powershell
$qgisPython = "C:\Program Files\QGIS 3.34.3\bin\python-qgis.bat"
$pluginDirectory = "$env:APPDATA\QGIS\QGIS3\profiles\default\python\plugins\routegis_autobuilder"
$deployedEnv = Join-Path $pluginDirectory ".env"
$beforeEnvHash = if (Test-Path -LiteralPath $deployedEnv) { (Get-FileHash -LiteralPath $deployedEnv -Algorithm SHA256).Hash } else { $null }
& $qgisPython -m pip install --user --upgrade "ezdxf==1.4.2" "httpx==0.27.2" "defusedxml>=0.7.1,<1" "PyYAML>=5.4,<7"
& $qgisPython -m pip install --user --force-reinstall --no-deps "dist\routegis_autobuilder-0.2.0-py3-none-any.whl"
New-Item -ItemType Directory -Path $pluginDirectory -Force | Out-Null
& robocopy "plugin" $pluginDirectory /E /XF ".env"
if ($LASTEXITCODE -ge 8) { throw "Plugin copy failed with robocopy exit code $LASTEXITCODE" }
$afterEnvHash = if (Test-Path -LiteralPath $deployedEnv) { (Get-FileHash -LiteralPath $deployedEnv -Algorithm SHA256).Hash } else { $null }
if ($beforeEnvHash -ne $afterEnvHash) { throw "Deployed plugin .env changed during installation" }
```

- [ ] **Step 7: Create and run a bounded actual-PyQGIS smoke script.** `tests/manual/qgis_smoke.py` accepts `--project-root` and `--plugin-dir`, initializes `QgsApplication`, imports all three clients, loads the installed plugin module, calls `classFactory` and `initGui`, opens/closes the Wizard, discovers and loads the generated QGZ, then always calls plugin unload and `QgsApplication.exitQgis()` in `finally`. Run it with:

```powershell
& $qgisPython "tests\manual\qgis_smoke.py" --project-root "output-qgis" --plugin-dir $pluginDirectory
```

The script asserts core/distribution/plugin versions are `0.2.0`; seven defaults are checked and legacy seven unchecked; both key fields use Password echo; basemaps are under `00_기본도`; and successful provider layers use the approved groups, styles, and source/license custom properties. It exercises Wizard search/filter and a stubbed VWorld refresh to confirm hidden/refreshing entries preserve checked IDs, inspects provider/dataset/license tooltip content, triggers a bounded fixture build attempt, and verifies both key fields are cleared afterward without network or credentials.
- [ ] **Step 8: Check installed environment health.** Run QGIS Python `pip check`; report unrelated pre-existing package conflicts separately and verify RouteGIS imports still work.
- [ ] **Step 9: Re-read the approved spec and record a requirement matrix.** Mark each provider, tiling, credential, catalog, QGIS, report, test, version, install, and manual-QA requirement `Closed` or `Still open`. Do not claim completion while any item remains open.

---

## Final Verification Commands

```powershell
& ".venv\Scripts\python.exe" -m pytest
& ".venv\Scripts\python.exe" -m compileall -q src plugin
& ".venv\Scripts\python.exe" -m build
& ".venv\Scripts\python.exe" -m pip check
git diff --check
```

Manual completion additionally requires the CLI fixture build, one bad-input CLI run, QGIS Python plugin smoke, generated QGZ inspection, installed `0.2.0` version alignment, and the credential-dependent live checks or an explicit unavailable-credential record.
