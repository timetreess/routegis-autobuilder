# VWorld Reference Taxonomy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** VWorld의 실시간 WFS 목록에 공식 WMS/WFS API 2.0 레퍼런스의 분야·공식명·분야 동의어를 sidecar로 결합해 `문화재`, `문화유산`, `국가유산` 검색으로 국가유산 3종을 모두 찾고 분야를 화면에 표시한다.

**Architecture:** `GetCapabilities`와 `LayerDefinition`은 다운로드의 유일한 운영 정의로 유지한다. 새 taxonomy 모듈은 체크인된 공식 레퍼런스 YAML을 검증해 불변 sidecar map으로 제공하고, `VWorldCatalog`가 이를 검색·표시 용도로만 결합한다. Wizard는 catalog API만 사용하며 taxonomy가 운영 `category`, Geometry, CRS, endpoint를 변경하지 못하게 한다.

**Tech Stack:** Python 3.9, PyYAML, dataclasses, pytest, PyQt/QGIS 3.34

---

## File Structure

- Create `src/routegis/resources/vworld_wfs_taxonomy.yaml`: 공식 레퍼런스 provenance, 분야 동의어, 확장 WFS ID 매핑.
- Create `src/routegis/providers/vworld/taxonomy.py`: taxonomy dataclass, 경로 해석, 중복·schema·provenance 검증 loader.
- Modify `src/routegis/providers/vworld/catalog.py`: sidecar 소유, taxonomy 조회, 확장 검색, 결정적 정렬 키.
- Modify `plugin/wizard.py`: 분야 prefix·tooltip·taxonomy 정렬 및 catalog 검색 API 사용.
- Modify `plugin/routegis_plugin.py`: taxonomy 초기화 실패를 critical dialog로 표시.
- Create `tests/fixtures/vworld_taxonomy.yaml`: 작은 정상 taxonomy fixture.
- Create `tests/unit/test_vworld_taxonomy.py`: loader/schema/provenance/sidecar 불변성 테스트.
- Modify `tests/unit/test_wizard_search.py`: 분야·동의어·미분류·카디널리티·정렬 회귀 테스트.
- Modify `README.md`: 공식 분류와 동의어 검색 동작 문서화.
- No `pyproject.toml` change is expected because `resources/*.yaml` is already packaged.

### Task 1: Taxonomy resource loader

**Files:**
- Create: `tests/fixtures/vworld_taxonomy.yaml`
- Create: `tests/unit/test_vworld_taxonomy.py`
- Create: `src/routegis/providers/vworld/taxonomy.py`

- [ ] Add a minimal valid fixture with quoted ISO date, one category alias set, three 국가유산 IDs, logical count and exact expanded count.
- [ ] Add failing tests for default/injected path loading, immutable normalized lookup, provenance fields, and the three 국가유산 entries.
- [ ] Add parameterized failing tests for missing file, malformed YAML, wrong root/field types, empty official name/category/alias, an undeclared category reference, expanded-count mismatch, exact duplicate YAML key, and case-insensitive WFS ID collision. Each must raise `ConfigurationError` containing the taxonomy path or offending ID.
- [ ] Run `& ".venv\Scripts\python.exe" -m pytest tests\unit\test_vworld_taxonomy.py -q` and confirm failure because the loader is absent.
- [ ] Implement frozen `TaxonomyEntry` and `VWorldTaxonomy` values plus `load_vworld_taxonomy(path: Path | None = None)`. Resolve the default with `resource_root() / "vworld_wfs_taxonomy.yaml"`, use a duplicate-detecting SafeLoader, normalize lookup keys with `casefold()`, expose layers/categories through `MappingProxyType`, validate all schema/provenance constraints, and wrap file/YAML/schema failures as `ConfigurationError`.
- [ ] Add assertions that `path=None` resolves under `routegis/resources` independently of the current working directory and that callers cannot mutate the loaded layer/category mappings.
- [ ] Re-run the targeted test and confirm success.

### Task 2: Complete checked-in official taxonomy

**Files:**
- Create: `src/routegis/resources/vworld_wfs_taxonomy.yaml`
- Test: `tests/unit/test_vworld_taxonomy.py`

- [ ] Fetch `https://www.vworld.kr/dev/v4dv_wmsguide2_s001.do` once for authoring only and extract each official table row's category, Korean name, and WFS IDs. Expand comma-separated WFS IDs into separate lowercase mapping keys. For the 23 rows whose WFS cell is empty, include the row's WMS IDs only after confirming each appears in the current WFS `GetCapabilities` with the same title; exclude style IDs and unmatched WMS-only IDs.
- [ ] Write provenance with `schema_version: 1`, source URL, quoted retrieval date `2026-09-23`, `source_logical_service_count: 169`, and the exact generated `expanded_wfs_id_count`.
- [ ] Declare every official category under `categories`, add `국가유산.aliases: [문화유산, 문화재]`, and verify these mappings exist: `lt_c_uo301`, `lt_p_dgmuseumart`, `lt_c_uo501`.
- [ ] Add a test that loads the packaged resource and asserts its declared expanded count equals `len(layers) == 174` and is at least the 169 documented logical services; every ID/category/name and alias is non-empty; every layer category is declared; and the three required IDs have `reference_category == "국가유산"`.
- [ ] Add sampled-reference assertions spanning at least six IDs and two categories, including all four WFS IDs from the multi-ID `한강회랑` row, so incomplete category extraction or failed row expansion cannot pass.
- [ ] Run the taxonomy tests and inspect the generated YAML for category transitions and multi-ID rows. Confirm no runtime scraper or new dependency is added.

### Task 3: Sidecar search and ordering

**Files:**
- Modify: `tests/unit/test_wizard_search.py`
- Modify: `src/routegis/providers/vworld/catalog.py`

- [ ] Add failing tests proving each of `문화재`, `문화유산`, and `국가유산` returns a set containing all three required 국가유산 IDs after live definitions are merged.
- [ ] Add failing tests for official-name search, category search, aliases scoped only to mapped category members, old name/internal ID/WFS ID search, blank/whitespace/case-folding behavior, and taxonomy-only IDs not materializing.
- [ ] Add failing tests proving a taxonomy-unknown live layer remains searchable, has no display category, sorts last, and retains every original `LayerDefinition` field.
- [ ] Add failing tests for default/live duplicate precedence and unequal base/live/taxonomy counts: the existing YAML definition/name wins, while all unique base/live IDs remain selectable.
- [ ] Add an explicit synthetic cardinality test with different base/live/taxonomy counts and overlaps; assert `len(catalog.all())` equals the case-insensitive union of base and live IDs and taxonomy-only IDs never appear.
- [ ] Run `& ".venv\Scripts\python.exe" -m pytest tests\unit\test_wizard_search.py -q` and confirm the new assertions fail.
- [ ] Audit every `VWorldCatalog(` call site. Keep fail-fast `ConfigurationError` behavior for CLI/core construction and use the plugin-specific dialog only around Wizard construction.
- [ ] Extend `VWorldCatalog.__init__` with keyword-only `taxonomy_path`; load the sidecar once. Add `taxonomy_for(definition)`, `matches(definition, query)`, and `sort_key(definition)`. The literal sort tuple is `(0 if taxonomy else 1, reference_category.casefold() if taxonomy else "", definition.name.casefold(), definition.data_id.casefold(), definition.id.casefold())`. Keep `matches_layer_query()` as the base name/internal-ID/data-ID helper used by `VWorldCatalog.matches()` and its existing external unit tests.
- [ ] Re-run taxonomy and wizard-search tests and confirm success.

### Task 4: Wizard classification rendering and startup error

**Files:**
- Modify: `plugin/wizard.py`
- Modify: `plugin/routegis_plugin.py`
- Temporary QA script: `$env:TEMP\routegis_taxonomy_smoke.py`

- [ ] Change `_render_layer_list()` to sort with `self.service.catalog.sort_key()`, prefix classified item text with `[reference_category]`, and leave unclassified item text unchanged. Tooltip lines are `분야: <reference_category>` or `분야: (분류 없음)`, then the current RouteGIS and VWorld IDs.
- [ ] Change `_filter_layer_list()` to call `self.service.catalog.matches(definition, needle)` without global query rewriting.
- [ ] Preserve item `Qt.UserRole` IDs and check-state handling unchanged so refresh/filter behavior remains stable.
- [ ] Import `QMessageBox` and `ConfigurationError` in `plugin/routegis_plugin.py`; catch taxonomy initialization errors around `RouteGISWizard(...)`, show a critical dialog containing only `필수 리소스 손상: vworld_wfs_taxonomy.yaml` and the first `ConfigurationError` sentence, leave `self.wizard` unset, and return without showing a window.
- [ ] Run `lsp_diagnostics` immediately after each Python file edit.
- [ ] Use QGIS Python offscreen with a fixture taxonomy to verify classified text, exact deterministic order, all three synonyms, unclassified-last behavior, check preservation over refresh/filter, and the startup critical-dialog path.

### Task 5: Documentation and regression verification

**Files:**
- Modify: `README.md`

- [ ] Document that the 187 live FeatureTypes are enriched by a checked-in snapshot of the official 169 logical-service reference; these counts have different meanings and server count changes are not errors.
- [ ] Document `[분야] 표시명`, official name/category/alias search, and the expected 국가유산 results for `문화재`, `문화유산`, `국가유산`.
- [ ] Run `& ".venv\Scripts\python.exe" -m pytest -q` and confirm the full suite passes.
- [ ] Run QGIS Python 3.9 compileall for `src\routegis` and `plugin`.
- [ ] Build `dist/routegis_autobuilder-0.1.0-py3-none-any.whl` and inspect the wheel to confirm `routegis/resources/vworld_wfs_taxonomy.yaml` is present.

### Task 6: Install and user-surface QA

**Files:**
- Install core wheel into the QGIS Python 3.9 user site.
- Deploy changed plugin Python files to `$env:APPDATA\QGIS\QGIS3\profiles\default\python\plugins\routegis_autobuilder\` without changing `.env`.

- [ ] Confirm the installed `.env` yields a non-empty key without printing it, VWorld is reachable, and no QGIS process is locking install files. If a lock exists, stop with the exact process/path blocker instead of terminating QGIS.
- [ ] Record the SHA-256 of `$env:APPDATA\QGIS\QGIS3\profiles\default\python\plugins\routegis_autobuilder\.env`, force-reinstall the built wheel with `python-qgis.bat -m pip install --user --no-deps --force-reinstall`, deploy only `plugin/wizard.py` and `plugin/routegis_plugin.py`, and confirm the `.env` hash is unchanged.
- [ ] In installed QGIS/PyQt, click `브이월드 목록 불러오기` and confirm every live FeatureType remains present even when taxonomy coverage is smaller.
- [ ] Search `문화재`, `문화유산`, and `국가유산`; confirm each result set contains `lt_c_uo301`, `lt_p_dgmuseumart`, and `lt_c_uo501` with `[국가유산]` labels.
- [ ] Confirm `개발제한구역`, `단층`, and raw ID searches still work, taxonomy-unknown layers remain visible, and a checked baseline item stays checked across refresh and all filters.
- [ ] Select a classified live layer and verify the original WFS definition reaches `DescribeFeatureType`/build without changes to operational `category`, endpoint, CRS, or Geometry.
- [ ] Confirm installed core and plugin hashes match source, the wheel contains taxonomy, delete `$env:TEMP\routegis_taxonomy_smoke.py`, and verify that path is absent.

No Git commit steps are included because this workspace is not a Git repository and the user did not request commits.
