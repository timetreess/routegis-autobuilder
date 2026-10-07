# VWorld Live Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** QGIS Wizard에서 인증된 VWorld WFS 전체 목록을 실시간으로 불러와 검색·선택하고, 선택한 실시간 레이어를 실제 프로젝트 생성 과정에서 다운로드한다.

**Architecture:** `VWorldClient`가 XML 기반 `GetCapabilities`와 `DescribeFeatureType`을 처리하고, `VWorldCatalog`가 기존 YAML 정의를 우선하여 실시간 정의를 제자리 병합한다. Wizard는 명시적 불러오기 버튼으로 병합된 목록을 다시 그리며, `ProjectService`는 같은 병합 카탈로그를 검증과 다운로드에 사용한다.

**Tech Stack:** Python 3.9, httpx 0.27, xml.etree.ElementTree, PyQt/QGIS 3.34, pytest

---

### Task 1: Capabilities XML parsing

**Files:**
- Create: `tests/fixtures/vworld_capabilities.xml`
- Modify: `tests/integration/test_vworld.py`
- Modify: `src/routegis/providers/vworld/client.py`

- [ ] Add a WFS 1.1.0 fixture containing namespaced and unnamespaced `FeatureType`, missing `Title`, and a non-prefix ID.
- [ ] Add failing tests proving `discover_layers()` returns `id == data_id`, Title fallback, WFS endpoint, `Unknown` geometry, and excludes missing Name.
- [ ] Run `& ".venv\Scripts\python.exe" -m pytest tests\integration\test_vworld.py -q` and confirm the new tests fail because discovery is absent.
- [ ] Add a shared HTTP response/retry helper plus XML local-name parsing and `discover_layers()`.
- [ ] Re-run the targeted test and confirm it passes.

### Task 2: DescribeFeatureType geometry resolution

**Files:**
- Modify: `tests/integration/test_vworld.py`
- Modify: `src/routegis/providers/vworld/client.py`

- [ ] Add failing tests for Polygon, LineString/Curve, Point GML property mappings and an unsupported schema.
- [ ] Add a failing fetch test proving an `Unknown` live definition is resolved before `SpatialLayer` creation.
- [ ] Run the targeted tests and confirm the new cases fail.
- [ ] Implement `describe_feature_type()` and use `dataclasses.replace()` inside `fetch_layer()` before GetFeature.
- [ ] Convert unresolved or failed type resolution into the existing per-layer failed `DownloadResult` path without blocking other layers.
- [ ] Re-run `tests/integration/test_vworld.py` and confirm all cases pass.

### Task 3: Merge live definitions into the active catalog

**Files:**
- Modify: `tests/unit/test_wizard_search.py`
- Modify: `src/routegis/providers/vworld/catalog.py`

- [ ] Add failing tests for in-place merge, YAML precedence by case-insensitive `data_id`, live `id` lookup, and baseline Data API layer preservation.
- [ ] Run `& ".venv\Scripts\python.exe" -m pytest tests\unit\test_wizard_search.py -q` and confirm failure.
- [ ] Implement `merge_live()` plus a read-only mapping/copy accessor used by validation.
- [ ] Re-run the targeted tests and confirm success.

### Task 4: Validate and download from the same catalog

**Files:**
- Modify: `tests/integration/test_pipeline.py`
- Modify: `src/routegis/app/project_service.py`

- [ ] Add a failing pipeline test with a live-only selected layer registered in `service.catalog` and a fixture provider.
- [ ] Run the exact new test and confirm it fails with `카탈로그에 없는 공간정보입니다`.
- [ ] Replace YAML reload validation with the active `self.catalog` contents.
- [ ] Re-run the targeted pipeline test and confirm the live-only layer is accepted and selected.

### Task 5: QGIS Wizard live-list UI

**Files:**
- Modify: `plugin/wizard.py`
- Test: installed QGIS 3.34 PyQt smoke script via `python-qgis.bat`

- [ ] Extract list rendering into one helper that preserves checked IDs, stores `id` and `data_id`, sorts by display name/WFS ID, and reapplies the current query.
- [ ] Add `브이월드 목록 불러오기` beside the search input.
- [ ] Implement the click handler using `.env` or temporary API Key and domain, with disabled button, wait cursor, bounded timeout, and `finally` restoration.
- [ ] On success, merge into `self.service.catalog`, refresh the list, and report the loaded total; on failure, leave the current list untouched and show a clear error.
- [ ] Run `lsp_diagnostics` for `plugin/wizard.py` and the changed core files.

### Task 6: Documentation, package, install, and manual QGIS QA

**Files:**
- Modify: `README.md`
- Build: `dist/routegis_autobuilder-0.1.0-py3-none-any.whl`
- Install: Python 3.9 user site and QGIS plugin profile

- [ ] Document that the initial seven items are local defaults and the button loads the live WFS catalog.
- [ ] Run the full pytest suite and Python 3.9 compile check.
- [ ] Build the wheel and force-reinstall it with `python-qgis.bat -m pip install --user --no-deps --force-reinstall`.
- [ ] Copy updated plugin files while preserving the deployed `.env` unchanged.
- [ ] In installed QGIS/PyQt, load the live catalog and verify `개발제한구역`, `단층`, and a raw WFS ID each return results, while checked baseline items remain checked.
- [ ] Verify a bad API Key leaves the baseline list intact and displays a useful error.
- [ ] Verify selecting a live layer reaches the project build path without catalog-validation rejection; use a bounded smoke request or fixture where real feature volume would make a live download unsafe.

No Git commit steps are included because this workspace is not a Git repository and the user did not request commits.
