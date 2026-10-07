# 기본 공간정보 다중 공급자 개편 설계

## 목표

RouteGIS의 기존 7개 기본 선택을 해제하고 다음 5개 주제를 기본 공간정보로 제공한다.

1. 생태자연도
2. 토지소유공간정보
3. 국가유산 지정·보호구역
4. 송전선로와 송전철탑·지지물
5. 하천 중심선과 하천구역·수면

사용자에게는 5개 주제로 보이지만 Geometry와 활용 목적이 다른 송전망과 하천을 분리하므로 실제 기본 출력은 7개 레이어다. CLI와 QGIS Wizard는 같은 공급자, 카탈로그, 오류 계약과 산출물 구조를 사용한다.

이 설계는 `2026-09-23-vworld-econatr-local-cache-design.md`의 생태자연도 로컬 ZIP 방식을 대체한다. 생태자연도는 사용자가 승인한 공공데이터포털 API 방식으로 구현한다.

## 확인된 현재 상태와 원인

- VWorld 실시간 `GetCapabilities` 187개 항목에서 생태자연도는 발견되지 않았다.
- 토지소유공간정보는 `dt_d160`, `MultiPolygon`이다.
- 국가유산 지정·보호구역은 `lt_c_uo301`, `MultiPolygon`이다.
- 기존 `[수자원] 하천망`은 `lt_c_wkmstrm`, `MultiPolygon`이며 하천 중심선 전체를 대표하지 않는다.
- 기존 장거리 검증 DXF의 노선 길이는 약 564.5 km이고 AOI는 약 55.1 km²다.
- VWorld WFS는 요청당 최대 1,000개 객체를 받고 현재 구현은 전체 레이어에 고정 100회 요청 예산을 사용한다. 장거리·고밀도 토지소유 레이어는 정상적인 공간분할만으로도 이 예산을 넘길 수 있다.
- 설치된 QuickOSM 2.4.1은 사용할 수 있지만 QGIS 런타임 의존, 큰 범위 처리, CLI 동등성 때문에 RouteGIS가 Overpass를 직접 호출하는 방식을 선택한다.

## 확정된 기본 레이어

| RouteGIS ID | 공급자 / 서비스 | `data_id` | 요청·응답 CRS | Geometry | 분류 |
| --- | --- | --- | --- | --- | --- |
| `ecology_nature` | `ecology` / `wfs` | `tbl_opn_eczm` | `EPSG:5186` / `EPSG:5186` | `MultiPolygon` | `ecology` |
| `land_ownership` | `vworld` / `wfs` | `dt_d160` | `EPSG:4326` / `EPSG:4326` | `MultiPolygon` | `land` |
| `heritage_protection` | `vworld` / `wfs` | `lt_c_uo301` | `EPSG:4326` / `EPSG:4326` | `MultiPolygon` | `heritage` |
| `power_line` | `osm` / `overpass` | `way[power=line]` | `EPSG:4326` / `EPSG:4326` | `MultiLineString` | `power` |
| `power_support` | `osm` / `overpass` | `node(w.lines)[power=tower|pole]` | `EPSG:4326` / `EPSG:4326` | `MultiPoint` | `power` |
| `river_centerline` | `osm` / `overpass` | `way[waterway=river|stream]` | `EPSG:4326` / `EPSG:4326` | `MultiLineString` | `water` |
| `river_area` | `osm` / `overpass` | `natural=water+water=river 또는 waterway=riverbank` | `EPSG:4326` / `EPSG:4326` | `MultiPolygon` | `water` |

기존 `cadastral`, 행정구역, 용도지역, 도로, 건축물과 실시간 VWorld 항목은 카탈로그에서 삭제하지 않는다. 다만 `basic_route` 기본 프로필에서는 모두 체크 해제한다. `lt_c_wkmstrm`도 검색·수동 선택은 가능하지만 새 기본에서는 제외한다.

## 카탈로그와 모델

`LayerDefinition`의 기존 `provider` 필드는 그대로 사용하고 다음 출처·라이선스 필드를 추가한다.

- `publisher`
- `dataset_id`
- `license_name`
- `license_url`

`data_id`는 실제 요청에 전달하는 레이어명·필터이고 `dataset_id`는 출처를 식별하는 외부 카탈로그 ID다. Ecology는 각각 `tbl_opn_eczm`과 `15057288`, OSM은 위 표의 필터와 `openstreetmap`, VWorld는 두 값 모두 공식 WFS/data ID를 사용한다.

새 네 필드는 필수이며 기본값을 만들지 않는다. 기존 YAML 정의에는 `provider: vworld`와 다음 공통 메타데이터를 명시한다. 실시간 VWorld 항목도 같은 값을 사용하고 `dataset_id=data_id`로 채운다.

- `publisher: 국토교통부 공간정보 오픈플랫폼(VWorld)`
- `license_name: VWorld OpenAPI 이용조건`
- `license_url: https://www.vworld.kr/dev/v4dv_wmsguide2_s001.do`

새 7개 기본 항목의 메타데이터는 다음 값으로 고정한다.

| RouteGIS ID | `publisher` | `dataset_id` | `license_name` | `license_url` |
| --- | --- | --- | --- | --- |
| `ecology_nature` | 국립생태원 | `15057288` | 이용허락범위 제한 없음 | `https://www.data.go.kr/data/15057288/openapi.do` |
| `land_ownership` | 국토교통부 공간정보 오픈플랫폼(VWorld) | `dt_d160` | VWorld OpenAPI 이용조건 | `https://www.vworld.kr/dev/v4dv_wmsguide2_s001.do` |
| `heritage_protection` | 국토교통부 공간정보 오픈플랫폼(VWorld) | `lt_c_uo301` | VWorld OpenAPI 이용조건 | `https://www.vworld.kr/dev/v4dv_wmsguide2_s001.do` |
| OSM 4개 항목 | OpenStreetMap contributors | `openstreetmap` | Open Database License (ODbL) 1.0 | `https://www.openstreetmap.org/copyright` |

이전 데이터 파일과의 묵시적 호환 분기는 추가하지 않는다. 저장소에 포함된 카탈로그와 fixture를 한 번에 새 스키마로 전환한다. `load_layer_catalog`의 허용 `service_type`은 `wfs`, `data`, `overpass`로 고정한다.

`VWorldCatalog`는 이름 때문에 공급자 전체 카탈로그를 표현하지 못하므로 `src/routegis/providers/catalog.py`의 `LayerCatalog`로 이동·변경한다. 호출부와 테스트도 함께 변경하며 이전 클래스 별칭은 만들지 않는다. VWorld 공식 분류·동의어인 `VWorldTaxonomy`는 기존 `providers/vworld` 아래에 남고 `provider=vworld` 항목에만 적용한다. Ecology와 OSM 항목의 분야·동의어는 일반 카탈로그 정의가 직접 제공한다.

이관 범위는 다음 파일과 계약을 포함한다.

- `models.py`: 출처·라이선스 필드
- `config.py`: 공급자 및 service type 검증, 새 설정 스키마
- `providers/catalog.py`: 통합 카탈로그와 VWorld 실시간 병합
- `project_service.py`, `plugin/wizard.py`, `providers/__init__.py`: 새 import와 공급자 라우팅
- `layer_catalog.yaml`, `basic_route.yaml`, `.env.example`: 새 정의와 기본값
- `qgis/project.py`, `resources/styles/*.qml`: 그룹·렌더러·스타일, 두 headless/native 경로의 Street·Hybrid 기본도 이동
- `report.py`, manifest 생성부, README: 공급원·라이선스·새 기본 프로필
- 기존 catalog/pipeline fixture와 테스트: 새 ID·그룹·출력 계약

실시간 VWorld 목록 병합은 `provider=vworld` 항목만 추가한다. 고정 Ecology·OSM 항목은 실시간 목록 갱신과 관계없이 항상 보인다. VWorld 목록 갱신 전후의 체크 상태는 기존처럼 RouteGIS ID로 보존한다.

## 공급자 라우팅

`ProjectService`는 선택 순서를 유지하며 `definition.provider`별 클라이언트를 지연 생성하고 실행 동안 재사용한다.

- `vworld`: 기존 `VWorldClient`
- `ecology`: 새 `EcologyClient`
- `osm`: 새 `OverpassClient`

테스트가 명시적으로 주입한 단일 `SpatialDataProvider`는 선택한 모든 레이어에 그대로 적용한다. 실제 실행에서 생성한 close 가능한 공급자는 `finally`에서 모두 닫는다.

알 수 없는 공급원, 자격 증명 누락, 공급자 초기화 실패는 해당 레이어의 `DownloadResult(status="failed")`로 격리한다. 다른 레이어 처리는 계속한다.

## 생태자연도 공급자

- 공식 데이터셋: 국립생태원 생태자연도 서비스
- 공공데이터포털 데이터셋 ID: `15057288`
- 서비스 루트: `https://apis.data.go.kr/B553084/ecoapi/EcologyzmpService`
- 레이어: `tbl_opn_eczm`
- 공식 좌표계: `EPSG:5186`
- 인증 환경변수: `DATA_GO_KR_SERVICE_KEY`

- WFS operation: `https://apis.data.go.kr/B553084/ecoapi/EcologyzmpService/wfs/getEcologyzmpWFS`
- 필수 파라미터: `serviceKey` (공공데이터포털 발급키를 URL encode하여 전달)
- 선택 파라미터: `typeName=tbl_opn_eczm`, `bbox`, `maxFeatures`, `eczmGr`, `ctprvnCd`, `signguCd`, `emdCd`
- `bbox`: EPSG:5186의 `minx,miny,maxx,maxy`
- `maxFeatures`: 최대 500
- 응답: WFS 1.0.0 GML2 `wfs:FeatureCollection`의 `gml:featureMember`
- feature element: `open:tbl_opn_eczm fid="tbl_opn_eczm.…"`
- Geometry: `open:geom/gml:MultiPolygon`의 `gml:polygonMember`, `outerBoundaryIs`·`innerBoundaryIs`, `gml:coordinates`
- 속성: `eczm_grad`, `vtn_evl_grad`, `smld_evl_grad`, `tpgrph_evl_grad`, `amplt_evl_grad`, `cln_symbl`, `plnt_cln_ttle`, `frph_agcl_code`, `precise_smld_ttle`, `census_ttle`, `tpgrph_ttle`
- 개발계정 기본 일일 트래픽: 10,000회

공식 공공데이터포털은 WMS·WFS·속성조회 세 operation을 제공하며 WFS 경로는 인증키 없이 호출할 때 HTTP 401을 반환하는 것까지 확인했다. 첨부 기술문서의 요청·응답 예제까지 기준으로 구현한다. XML namespace URI로 요소를 찾고 prefix 문자열에는 의존하지 않는다. `fid`를 안정 ID로 사용하며 누락되거나 같은 `fid`의 Geometry·속성이 서로 다르면 응답 스키마 오류로 해당 레이어를 실패시킨다. `geom`은 `srsName`의 EPSG:5186을 확인한 뒤 GML2 coordinate tuple을 MultiPolygon으로 파싱한다. 닫히지 않은 ring은 닫고, 유효하지 않은 ring·알 수 없는 CRS·필수 Geometry 누락은 조용히 버리지 않는다. 문서에 없는 JSON 전환 파라미터를 추측하지 않고 WMS 이미지 대체 경로도 만들지 않는다.

각 tile은 `maxFeatures=500`으로 조회한다. `gml:featureMember` 수가 500이면 결과 포화로 간주하여 tile을 이분할하고, 500 미만이면 완료로 간주한다. API가 별도의 전체 건수나 page offset을 제공하지 않으므로 포화 tile 자체는 결과에 병합하지 않고 child tile 응답만 사용한다.

국립생태원 데이터의 이용허락범위는 공공데이터포털의 `이용허락범위 제한 없음`이며 발행기관, 데이터셋 ID, 공식 상세 URL과 이용허락 정보를 manifest·보고서·tooltip에 기록한다.

Wizard에는 공공데이터포털 서비스키 임시 입력란을 추가한다. 우선순위는 환경변수, 설치 플러그인 `.env`, 임시 입력 순서다. 키는 `QSettings`, 프로젝트, manifest, 보고서와 로그에 저장하지 않는다.

## Overpass 공급자

기본 endpoint는 `https://overpass-api.de/api/interpreter`로 설정하고 구성 파일에서 변경할 수 있게 한다. 쿼리는 JSON 응답과 `out geom`을 사용한다.

### 송전망

각 EPSG:4326 타일 bbox `(south,west,north,east)`에 대해 다음 하나의 Overpass QL union query를 사용한다.

```overpass
[out:json][timeout:180];
way["power"="line"](south,west,north,east)->.lines;
(
  .lines;
  node(w.lines)["power"~"^(tower|pole)$"];
);
out body geom;
```

따라서 지지물은 `power=line` way의 실제 구성 node이면서 `power=tower|pole` 태그가 있는 객체로 제한된다. node 조회에는 별도 bbox를 적용하지 않아 타일과 교차하는 송전 way의 전체 Geometry와 구성 지지물을 함께 보존한다. 독립적인 배전선 `power=minor_line`과 그 배전주는 기본 범위에 포함하지 않는다.

### 하천

각 타일에 다음 query를 사용한다.

```overpass
[out:json][timeout:180];
(
  way["waterway"~"^(river|stream)$"](south,west,north,east);
  way["natural"="water"]["water"="river"](south,west,north,east);
  relation["natural"="water"]["water"="river"](south,west,north,east);
  way["waterway"="riverbank"](south,west,north,east);
  relation["waterway"="riverbank"](south,west,north,east);
);
out body geom;
```

중심선은 `waterway=river|stream`, 하천구역·수면은 `natural=water`이면서 `water=river`, 그리고 레거시 `waterway=riverbank`로 분리한다.

OSM 객체의 안정적인 식별자는 `node|way|relation` 유형과 숫자 ID의 조합이다. 타일 경계 중복은 이 식별자로 제거한다. way와 relation Geometry는 응답의 전체 Geometry를 보존하고 최종적으로 AOI와 교차 여부를 확인한다.

node는 `lat`·`lon`, way는 `geometry` 좌표 배열로 변환한다. multipolygon relation은 `members`의 `role=outer|inner` way Geometry를 endpoint로 결합하여 닫힌 ring을 만들고 inner ring을 이를 포함하는 outer에 배치한다. 결합할 수 없는 ring, 자기교차로 복구할 수 없는 polygon 또는 필수 member Geometry 누락은 객체를 조용히 버리지 않고 해당 레이어를 실패시킨다.

Overpass 요청 CRS는 항상 `EPSG:4326`이며 bbox 순서는 `south,west,north,east`다. 응답 최대 크기는 64 MiB, timeout은 180초다. `OverpassClient`는 VWorld 상수를 공유하지 않고 자체 `TRANSIENT_STATUS = {429, 502, 503, 504}`를 사용한다. `retry: 2`는 최초 시도 뒤 최대 2회 재시도, 즉 타일당 최대 3번의 전송 시도를 뜻하며 transport timeout에도 동일하게 적용한다. relation Geometry가 존재하면 동일 relation의 member way를 독립 polygon으로 중복 출력하지 않고, 객체 ID 제거 후에도 동일한 정규화 Geometry와 속성 fingerprint가 남으면 한 번 더 중복 제거한다.

### 라이선스

GeoPackage, Shapefile, QGIS 프로젝트와 처리 보고서에 다음을 기록한다.

- `© OpenStreetMap contributors`
- `https://www.openstreetmap.org/copyright`
- `Open Database License (ODbL) 1.0`

OSM 원본과 파생 데이터의 재배포 조건을 README에도 명시한다.

## 장거리 AOI 타일 계획

모든 공급자는 `src/routegis/gis/tiling.py`의 공통 `AoiTilePlanner`가 만든 비중복 타일을 각 공급자의 `fetch_layer` 내부에서 사용한다. 외부 `SpatialDataProvider.fetch_layer(definition, aoi)` 계약은 변경하지 않는다.

1. AOI를 미터 단위 출력 CRS에서 계획한다.
2. 공급자·레이어별 최대 타일 변 길이로 정사각 격자를 만든다.
3. AOI Geometry와 교차하는 격자만 유지한다.
4. 각 격자 Geometry를 요청 CRS로 변환한 뒤 bbox를 요청에 사용한다.
5. 응답이 공급자 제한에 포화되면 해당 타일만 이분할한다.

초기값은 다음과 같다.

- 고밀도 VWorld `land_ownership`: 5 km
- 다른 VWorld polygon: 10 km
- Ecology polygon: 10 km
- Overpass: 25 km

이 값은 애플리케이션 구성으로 관리하지만 Wizard 옵션으로 노출하지 않는다.

요청 예산은 고정 100회가 아니라 `min(absolute_maximum, max(configured_minimum, initial_tile_count * per_tile_budget))`로 계산한다. 초기값은 minimum 100, tile당 16회, 절대 상한 5,000회다. 타일당 16회는 하나의 초기 타일이 포화될 때 3세대 이분할 트리의 모든 node 요청 15회와 한 번의 transient 재시도 여유를 뜻한다.

예산 카운터는 실제 HTTP 전송을 시도할 때마다 1 증가한다. 최초 타일, 포화 후 생성한 모든 child 타일, transient 재시도 모두 같은 레이어 전역 예산을 소비한다. `AoiTilePlanner`의 초기 격자 생성 자체는 소비하지 않는다. 최대 분할 깊이는 초기 타일 기준 32로 유지하지만 예산 또는 최소 bbox 크기가 먼저 도달하면 중단한다. 564.5 km 노선에서 약 113개의 5 km 초기 타일이 생성되면 기본 예산은 1,808회다.

기존 `vworld.max_pages`는 제거하고 다음 설정을 추가한다.

- `download.request_budget_minimum: 100`
- `download.request_budget_per_tile: 16`
- `download.request_budget_maximum: 5000`
- `download.max_split_depth: 32`
- `download.minimum_tile_size_m: 50`
- `download.max_response_bytes: 67108864`
- `vworld.land_ownership_tile_size_m: 5000`
- `vworld.default_tile_size_m: 10000`
- `ecology.tile_size_m: 10000`, `timeout: 30`, `retry: 3`, `page_size: 500`
- `osm.endpoint`, `tile_size_m: 25000`, `timeout: 180`, `retry: 2`

VWorld의 1,000개 제한, Ecology 공식 제한, Overpass timeout/maxsize는 각각의 클라이언트가 적용한다. 타일은 순차 요청하여 공용 API 동시성 제한을 피한다. transient HTTP 상태와 transport timeout만 제한적으로 재시도하고 서비스 오류는 그대로 보고한다.

## 병합과 완전성

- 타일 경계 중복은 VWorld·Ecology·OSM의 공급원별 안정 ID로 제거한다.
- 동일 ID의 내용이 서로 다르면 조용히 하나를 버리지 않는다. 공급원, ID와 타일을 포함한 오류로 해당 레이어를 실패시킨다.
- 최종 feature는 AOI와 교차해야 하지만 토지 필지·보호구역·수면·선로의 원본 Geometry는 절단하지 않는다. 보고서에는 `AOI 교차 객체를 원형 보존`했다고 명시하여 AOI 밖 Geometry가 함께 보일 수 있음을 알린다.
- 한 타일이라도 재시도 후 실패하면 성공한 타일만 저장하지 않고 해당 레이어 전체를 `failed`로 처리한다.
- 실패한 레이어는 GeoPackage, Shapefile과 QGIS layer tree에 포함하지 않는다.
- 다른 레이어는 계속 처리하며 프로젝트는 `COMPLETED_WITH_WARNINGS`가 될 수 있다.

## Wizard와 기본 선택

`basic_route.yaml`은 위 7개 RouteGIS ID만 포함한다. Wizard는 첫 렌더링에서 이 7개만 체크한다.

공간정보 화면은 다음을 제공한다.

- VWorld API Key 임시 입력
- 공공데이터포털 서비스키 임시 입력
- VWorld에만 적용되는 등록 Domain
- 공급원·이름·동의어·RouteGIS ID·원본 ID 검색
- VWorld 실시간 목록 갱신

두 Key 입력란은 Password echo를 사용하고 프로젝트 생성 후 비운다. 환경변수, 설치 플러그인 `.env`, 임시 입력 순으로 해석하며 `DATA_GO_KR_SERVICE_KEY`는 새 `load_data_go_kr_service_key`가 기존 VWorld helper와 같은 계약으로 읽는다.

목록 label은 `[분야] 이름`을 유지하고 tooltip에 공급원, 원본 ID, 발행기관과 라이선스를 표시한다. 검색 대상은 공급원, 표시명, 동의어, RouteGIS ID와 원본 data ID다. 검색이나 VWorld 갱신으로 항목이 숨겨져도 체크 상태는 유지한다.

## QGIS 그룹과 스타일

그룹 순서는 다음과 같다.

1. `00_기본도`
2. `01_노선`
3. `02_토지`
4. `03_생태환경`
5. `04_국가유산`
6. `05_수자원`
7. `06_전력시설`
8. `07_행정구역`
9. `08_토지이용`
10. `09_시설물`
11. `99_원본자료`

분류 매핑은 `land`, `cadastral`, `ecology`, `heritage`, `water`, `power`, `administrative`, `landuse`, `facility`, `route`, `source`를 명시적으로 처리한다.

- 기존 `cadastral` 항목의 category와 `shp/cadastral/...` 출력 경로는 호환성을 위해 유지한다. 새 `land`와 기존 `cadastral`은 QGIS에서만 모두 `02_토지`에 배치한다.
- 새 category는 각각 `shp/land`, `shp/ecology`, `shp/heritage`, `shp/water`, `shp/power`에 출력하고 `PROJECT_DIRECTORIES`에 이 경로를 추가한다. 기존 `facility`는 `shp/facility`를 유지하되 QGIS 그룹만 `09_시설물`로 이동한다.
- VWorld Street와 VWorld Hybrid raster는 tree root가 아니라 `00_기본도`에 배치한다.
- 실시간 미분류 VWorld 항목과 기존 `facility`는 `09_시설물`에 배치한다.

스타일은 Geometry를 구분하는 최소 고정 스타일을 사용한다. 색상과 파일 매핑은 다음으로 고정한다.

- `land_ownership`: 기존 `cadastral.qml` 재사용, 투명 fill과 `#616161` 0.8 pt 경계
- `ecology_nature`: 새 `ecology.qml`, `#4CAF50` alpha 80 fill과 `#2E7D32` 0.8 pt 경계
- `heritage_protection`: 새 `heritage.qml`, `#9C27B0` alpha 80 fill과 `#6A1B9A` 0.8 pt 경계
- `river_centerline`: 새 `river_line.qml`, `#1976D2` 1.2 pt line
- `river_area`: 새 `river_area.qml`, `#42A5F5` alpha 70 fill과 `#1976D2` 0.8 pt 경계
- `power_line`: 새 `power_line.qml`, `#8D4A2D` 1.2 pt line
- `power_support`: 새 `power_support.qml`, `#8D4A2D` 2.5 mm 원형 marker

`qgis/project.py`의 style resolver는 새 레이어 ID별로 위 파일을 선택하고 기존 레이어는 현재 category 기반 파일을 유지한다. Headless `_renderer`도 레이어 ID와 Geometry 종류별로 같은 색상·선폭·marker 크기를 사용한다. 공식 속성 스키마를 확인하기 전에는 등급·전압·하천규모별 분류 스타일을 추측하지 않는다.

## 보고서와 진행 정보

`DownloadResult`에 다음 필드를 직접 추가한다.

- `initial_tile_count: int = 0`
- `request_count: int = 0`
- `successful_tile_count: int = 0`
- `failed_tile_count: int = 0`
- `failed_tile_bbox: tuple[float, float, float, float] | None = None`

`failed_tile_bbox`는 프로젝트 출력 CRS 좌표로 저장한다. 기존 `error`가 마지막 오류를 나타내므로 별도 `last_error` 필드는 추가하지 않는다. 공급원·원본 ID·발행기관·라이선스는 `DownloadResult`에 중복 저장하지 않고 결과 조립 시 선택된 `LayerDefinition`과 join한다.

`initial_tile_count`는 최초 AOI 격자 수, `request_count`는 retry와 포화 parent를 포함한 실제 HTTP 전송 시도 수, `successful_tile_count`는 최종 결과에 채택한 비포화 leaf tile 수다. 포화되어 분할한 parent는 성공 tile로 세지 않는다. 순차 처리 중 첫 terminal tile 실패에서 중단하므로 `failed_tile_count`는 성공 시 0, 실패 시 1이며 아직 처리하지 않은 tile은 실패 수에 넣지 않는다.

각 레이어 manifest·JSON·HTML 행의 field name은 다음으로 고정한다.

- `id`, `name`, `status`, `feature_count`, `error`
- `provider`, `data_id`, `dataset_id`, `publisher`, `license_name`, `license_url`
- `initial_tile_count`, `request_count`, `successful_tile_count`, `failed_tile_count`, `failed_tile_bbox`

API Key, 전체 요청 URL의 query string과 사용자 `.env` 경로는 기록하지 않는다.

## 버전과 설치

- 패키지 버전: `0.2.0`
- `pyproject.toml`, `routegis.__version__`, 플러그인 `metadata.txt`를 함께 변경한다.
- QGIS Python 3.9 사용자 영역에 새 wheel과 필요한 순수 Python 의존성을 설치한다.
- HTTP 처리는 기존 `httpx`를 사용하므로 새 런타임 의존성은 추가하지 않는다.
- 플러그인 설치 폴더의 기존 `.env`는 덮어쓰지 않는다.
- `.env.example`에 빈 `DATA_GO_KR_SERVICE_KEY=`를 추가한다.
- 설치 후 QGIS 3.34.3의 실제 Python에서 core/distribution/plugin 버전, `classFactory`, `initGui`, provider import를 smoke test한다.

## 자동 검증

### 신규·변경 fixture

- `tests/fixtures/spatial_mock.json`: 기존 `vworld_mock.json`을 대체하는 pipeline fixture다. top-level CRS는 단일 `EPSG:5186`으로 유지하고 `FixtureProvider` 스키마는 바꾸지 않는다. 공급자 클라이언트가 이미 출력 CRS로 정규화한 최종 `SpatialLayer`를 모사한다.
- `spatial_mock.json`은 새 7개 레이어의 성공 payload 외에 기존 `cadastral` 성공 payload와 `building` 오류 payload를 유지하여 기존 부분 실패 회귀 테스트도 계속 지원한다.
- 새 최소 Geometry는 `ecology_nature`, `land_ownership`, `heritage_protection`, `river_area`에 닫힌 MultiPolygon 한 개씩, `power_line`, `river_centerline`에 MultiLineString 한 개씩, `power_support`에 MultiPoint 한 개를 둔다. 송전 line은 `[(199800,489000),(200600,489200)]`, support는 그 위의 `(200000,489050)`, `(200400,489150)`, river line은 `[(199800,489300),(200600,489100)]`, river area는 `(199900,489150)`부터 `(200500,489350)`까지의 닫힌 사각형을 사용한다. 세 polygon 주제도 같은 sample AOI 안에서 서로 조금씩 어긋난 닫힌 사각형을 사용한다.
- `tests/fixtures/ecology_wfs_response.xml`: 공식 기술문서의 WFS 1.0.0 `FeatureCollection`·GML2 sample을 손으로 옮긴 한 feature 응답이며 `fid`, 외부 ring, 내부 ring과 공식 속성을 포함한다. 실제 키와 전체 요청 URL은 넣지 않는다.
- `tests/fixtures/overpass_power.json`: `power=line` way, 그 way의 tower·pole node, 제외 대상 독립 pole과 `minor_line`을 포함하는 hand-curated Overpass `out geom` 응답이다.
- `tests/fixtures/overpass_river.json`: river·stream way, river multipolygon relation과 member way, legacy riverbank를 포함하는 hand-curated 응답이다.
- 500개 포화 Ecology 응답과 HTTP 오류 payload는 큰 fixture 파일을 추가하지 않고 테스트 helper가 위 XML feature를 반복하거나 최소 오류 XML을 inline 생성한다.

### 카탈로그와 Wizard

- `basic_route`가 새 7개 ID와 정확히 일치한다.
- 기존 7개 항목은 목록에 남지만 기본 체크되지 않는다.
- 공급원·원본 ID·분야·동의어 검색과 VWorld 갱신 후 체크 보존.

### VWorld

- `dt_d160`, `lt_c_uo301` 정의와 Geometry.
- 100개를 넘는 장거리 토지소유 타일을 완전히 병합한다.
- 동적 예산과 절대 상한.
- 한 타일 실패 시 레이어 원자적 실패.
- 경계 중복 제거와 동일 ID 내용 충돌.

### Ecology

- 공식 operation `wfs/getEcologyzmpWFS`, EPSG:5186 bbox와 `maxFeatures<=500` 요청.
- 인증키 전달과 비노출.
- `tests/fixtures/ecology_wfs_response.xml`의 공식 WFS 1.0.0/GML2 envelope parsing, namespace 독립 탐색, `fid` 중복 제거, `gml:MultiPolygon` 외·내부 ring과 CRS 변환.
- 500개 포화 응답은 parent tile을 버리고 child tile만 병합한다.
- 키 누락, 서비스 오류, 빈 결과와 타일 실패 격리.

### Overpass

- `tests/fixtures/overpass_power.json`, `overpass_river.json`과 `httpx.MockTransport`를 사용하여 네트워크 없이 검증.
- `power=line`과 해당 way의 `tower|pole`만 포함.
- `minor_line`과 독립 배전 pole 제외.
- `river|stream` 중심선과 river area·riverbank polygon parsing.
- node/way/relation ID 중복 제거, Geometry 변환, timeout와 서비스 오류.
- ODbL attribution 출력.

### 통합 출력

- 새 7개 GeoPackage 테이블과 Shapefile 경로.
- 새 QGIS 그룹 순서·스타일·레이어 배치.
- manifest와 보고서의 공급원·라이선스·타일 통계.
- 개별 레이어 실패 시 `COMPLETED_WITH_WARNINGS`와 불완전 레이어 미출력.
- 기존 CAD, CRS, staging, publish, credential redaction 회귀 테스트.

기존 `FixtureProvider` fixture는 공급자 프로토콜 응답이 아니라 최종 `SpatialLayer` 역할을 하므로 전체 pipeline/QGZ 산출물 테스트에 사용한다. VWorld·Ecology·Overpass의 HTTP 요청·응답 계약은 각각 별도 fixture와 `MockTransport`로 검증한다. `vworld_wfs_taxonomy.yaml`과 taxonomy fixture는 VWorld 전용으로 유지한다.

### 회귀 테스트 이관 목록

- `tests/integration/test_pipeline.py`: fixture 경로를 `spatial_mock.json`으로 변경한다. 기존 cadastral category와 `shp/cadastral/cadastral.shp` assertion은 유지하고 그룹 assertion만 `02_지적`에서 `02_토지`로 바꾼다.
- 같은 pipeline 테스트의 VWorld Street·Hybrid root-child assertion은 `00_기본도` 아래의 Street layer와 Hybrid subgroup assertion으로 변경하고 headless layer order도 새 tree 순서에 맞춘다.
- 새 pipeline case는 기본 프로필 7개가 모두 GeoPackage·Shapefile·QGZ에 생성되고 각 새 그룹·스타일·보고서 metadata와 tile 통계가 존재하는지 검증한다.
- `tests/unit/test_wizard_search.py`: import를 `routegis.providers.catalog.LayerCatalog`로 변경하고 기존 `VWorldCatalog` 별칭은 사용하지 않는다. 새 필수 출처 필드, 공급원·dataset ID 검색, 7개 기본 체크와 VWorld 갱신 후 체크 보존을 검증한다.
- `tests/integration/test_vworld.py`: 모든 `LayerDefinition` 생성에 필수 출처 필드를 추가한다. 기존 `cadastral`·`facility` category와 SHP 경로는 유지하고 동적 request budget·통계·원자적 실패 case를 추가한다.
- `tests/integration/test_ecology.py`: 공식 요청 파라미터, GML2 parser, `fid` 중복, ring, CRS, 500개 포화 분할, 키 비노출과 오류 격리를 검증한다.
- `tests/integration/test_overpass.py`: 정확한 power·river query, node-way 연계, relation/member 중복 제거, retry status와 attribution을 검증한다.
- `tests/unit/test_tiling.py`: 비중복 격자, AOI 교차 필터, 요청 CRS bbox, 이분할, 최소 bbox와 예산 상한을 검증한다.
- 저장소 전체의 직접 `LayerDefinition(...)` 생성은 네 출처 필드를 명시적으로 추가하며 test-only 기본값이나 compatibility constructor를 만들지 않는다.

## 수동 QGIS 검증

1. QGIS 3.34.3에서 RouteGIS 0.2.0을 로드한다.
2. 공간정보 화면에서 새 7개만 기본 체크되고 기존 항목은 체크 해제됐는지 확인한다.
3. 검색과 VWorld 목록 갱신 후 체크 상태가 유지되는지 확인한다.
4. 작은 fixture 프로젝트로 7개 레이어, 그룹, 스타일, 보고서를 확인한다.
5. 기존 약 564.5 km DXF로 토지소유 레이어를 실행하여 고정 100회 제한 없이 완료되는지 확인한다.
6. 실제 VWorld 키로 토지소유와 국가유산을 smoke test한다.
7. 공공데이터포털 키가 제공되면 생태자연도 실서비스를 smoke test한다. 키가 없으면 mock fixture 검증까지만 수행하고 결과에 명시한다.
8. Overpass에서 송전선로·지지물과 하천 중심선·하천구역이 생성되고 OSM attribution이 보이는지 확인한다.

## 범위 제외

- QuickOSM 플러그인 호출 또는 버전 의존
- 배전선 `power=minor_line`과 독립 배전주 전체 수집
- VWorld `lt_c_wkmstrm`을 대하천 중심선으로 재해석
- 생태자연도 WMS 이미지 대체 출력
- 불완전 타일 결과를 성공 레이어로 저장
- 전국 단위 전체 데이터 다운로드·영구 캐시
- API Key 저장 또는 자동 발급
