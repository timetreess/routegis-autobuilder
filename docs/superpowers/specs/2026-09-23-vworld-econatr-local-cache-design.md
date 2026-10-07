# VWorld 생태자연도 로컬 캐시 설계

## 목표

VWorld 공간정보 다운로드에서 사용자가 받은 `ECONATR_*.zip` 파일을 전역 관리 폴더에 보관하면 RouteGIS가 이를 `[환경·생태] 생태자연도` 한 항목으로 제공하고, 선택한 사업 AOI와 겹치는 객체만 병합·절단하여 기존 GeoPackage, Shapefile, QGZ, 보고서에 포함한다.

## 확정된 운영 방식

- 실제 ZIP 다운로드는 자동화하지 않는다. VWorld 로그인과 Windows 전용 라온K 다운로드 프로그램이 필요하고 직접 다운로드 URL이 공개되지 않기 때문이다.
- 사용자는 VWorld에서 공식 ZIP을 내려받아 하나의 전역 관리 폴더에 넣는다.
- 기본 폴더는 `~/RouteGIS/econatr`이며 QGIS Wizard에서 다른 폴더를 선택할 수 있다.
- Wizard는 성공적으로 프로젝트를 생성한 뒤 선택한 폴더를 `QSettings`에 저장한다. API Key와 달리 데이터 경로는 비밀정보가 아니다.
- CLI는 `--econatr-dir`로 같은 폴더를 지정하며, 생략하면 기본 폴더를 사용한다. UI와 CLI 모두 `~`를 확장하고 절대경로로 정규화하여 `ProjectConfig.econatr_directory`에 전달한다.
- 폴더 존재 여부와 ZIP 검증은 `econatr`가 실제 선택된 경우에만 수행한다.
- ZIP을 교체하면 별도 색인이나 캐시 무효화 없이 다음 실행부터 새 파일을 읽는다.
- UI에는 시도별 17개 항목이 아니라 하나의 `econatr` 레이어만 표시한다.

## 공식 데이터 계약

- 데이터셋 ID: `20230915DS00004`
- 공식 상세 페이지: `https://www.vworld.kr/dtmk/dtmk_ntads_s002.do?svcCde=MK&dsId=20230915DS00004`
- 파일 패턴: `ECONATR_*.zip`
- 현재 공식 패키지 수: 17개 시도
- 공식 좌표계: EPSG:5186
- 공식 갱신주기: 변경발생시
- 라이선스: CC BY-NC-ND 2.0 KR
- 제공기관: 기후에너지환경부

RouteGIS는 라이선스 조건을 UI tooltip, README, 프로젝트 manifest에 기록한다. 데이터 이용 적합성을 판단하거나 라이선스 제한을 우회하지 않는다.

## 카탈로그와 UI

`layer_catalog.yaml`에 다음 의미의 고정 항목을 추가한다.

- ID: `econatr`
- 표시명: `생태자연도`
- provider: `econatr`
- service type: `shp`
- data ID: `ECONATR`
- geometry: `MultiPolygon`
- category: `ecology`
- request/response CRS: `EPSG:5186`
- endpoint: 공식 상세 페이지
- publisher: `기후에너지환경부`
- dataset ID: `20230915DS00004`
- license name: `CC BY-NC-ND 2.0 KR`
- license URL: `https://creativecommons.org/licenses/by-nc-nd/2.0/kr/`

이를 위해 `LayerDefinition`에 기존 항목을 깨지 않는 선택 필드 `publisher`, `dataset_id`, `license_name`, `license_url`을 추가하고 `load_layer_catalog`가 `service_type: shp`를 허용하게 한다.

이 항목은 ZIP이 없어도 목록에 보인다. 그래야 사용자가 기능을 발견하고 필요한 파일을 준비할 수 있다.

Wizard 공간정보 화면에 `생태자연도 ZIP 폴더` 경로와 `찾기` 버튼을 추가한다. 항목 label은 `[환경·생태] 생태자연도`이며 tooltip에는 공식 출처, 데이터셋 ID, 관리 폴더, 라이선스 경고를 표시한다.

ZIP이 없는 상태에서 선택하지 않으면 기존 프로젝트 생성에는 영향이 없다. 선택한 상태에서 필요한 ZIP을 찾지 못하면 해당 레이어만 실패 처리하고 프로젝트는 `COMPLETED_WITH_WARNINGS`로 생성한다.

## 로컬 SHP 공급자

새 `EconatrProvider`는 기존 `SpatialDataProvider` 계약을 구현한다.

읽기 의존성은 Python 3.9를 지원하는 순수 Python 패키지 `pyshp==3.1.6`으로 고정한다. QGIS/GDAL 설치 여부와 무관하게 CLI와 테스트가 같은 코드를 실행해야 한다.

### 발견

1. 관리 폴더 바로 아래의 `ECONATR_*.zip`을 대소문자 구분 없이 정렬하여 찾는다.
2. 각 ZIP 안의 모든 `.shp`를 재귀적으로 찾는다. ZIP 하나에 한 파일만 있다는 가정을 하지 않는다.
3. member 이름은 `\\`도 `/`로 정규화한다. 절대경로, drive/UNC 경로, `..`, NUL, 정규화 후 중복 이름은 거부한다.
4. 같은 stem과 경로를 가진 `.shx`, `.dbf`, `.prj`를 필수 sidecar로 요구하고 `.cpg`는 선택으로 읽는다.
5. 암호화 entry와 비정상 archive를 거부한다. ZIP당 member 10,000개, component당 8 GiB, 실행당 선택 component 32 GiB를 상한으로 둔다.
6. `extract()`/`extractall()`은 사용하지 않는다. 필요한 sidecar를 bounded chunk stream으로 임시 폴더의 생성된 안전한 파일명에 복사하고, 실제 복사 바이트 수와 ZIP CRC를 검증한다.

### 공간 선별

1. 사업 AOI를 EPSG:5186으로 변환한다.
2. 각 component set의 `.prj`를 크기 제한 내에서 직접 읽어 `CRS.from_epsg(5186)`과 동등한지 먼저 검증한다. CRS가 틀린 파일을 bbox 불일치로 조용히 건너뛰면 안 된다.
3. ZIP 내부 SHP의 100-byte header에서 file code `9994`, version `1000`, polygon 계열 shape type, 유한하고 순서가 올바른 bbox, 타당한 선언 파일 길이를 검증한다.
4. 검증된 bounding box가 AOI bbox와 겹치지 않으면 DBF와 geometry를 읽지 않는다.
5. 겹치는 SHP component set만 실행별 임시 폴더로 복사하여 읽는다.

### 읽기와 정규화

- `.cpg`가 있으면 공백·대소문자를 정규화하고 `codecs.lookup`으로 검증한다. `949`, `65001` 같은 알려진 숫자 표기도 정규화하며 strict decode를 사용한다.
- `.cpg`가 없으면 UTF-8, CP949, EUC-KR의 서로 다른 codec 순서로 Reader를 매번 새로 열어 재시도한다. 실패한 시도의 feature와 중복제거 상태는 모두 폐기한다.
- `Reader.iterShapeRecords(bbox=...)`로 record를 한 건씩 처리한다. `shapes()`, `shapeRecords()`, Reader 전체 `__geo_interface__`처럼 전체 자료를 메모리에 올리는 API는 사용하지 않는다.
- DBF 날짜는 ISO 문자열로 바꾸고, downstream writer가 지원하는 문자열·숫자·불리언·NULL만 속성값으로 유지한다.
- shape record bbox로 한 번 더 prefilter한 뒤 Shapely geometry를 2D로 강제한다.
- 유효하지 않은 polygon은 `make_valid`한다. Polygon, MultiPolygon, GeometryCollection을 재귀적으로 순회해 polygon 부분만 수집하고 하나의 MultiPolygon으로 정규화한다.
- geometry가 없는 레코드는 오류로 처리한다.
- 원본 DBF 속성은 유지한다.

### 병합, 중복 제거, AOI 절단

- 모든 non-empty AOI feature의 union을 한 번 계산하여 EPSG:5186으로 변환한다.
- 모든 intersecting SHP의 feature를 하나의 `SpatialLayer`로 합친다.
- source 중복 key는 정규화한 2D WKB와 type-tag를 포함해 정렬한 scalar 속성의 fingerprint로 만든다. ZIP/member/record 정렬 순서에서 처음 만난 record를 유지한다.
- 중복을 제거한 geometry를 실제 AOI geometry와 교차하여 AOI 밖 부분을 제거한다.
- 교차 결과가 선이나 점만 남으면 버리고, polygon 부분을 재귀적으로 수집해 MultiPolygon으로 정규화한다. clipping 후 같은 fingerprint가 생기면 다시 중복 제거한다.
- 최종 layer는 AOI 출력 CRS로 변환하며 `source="vworld_download"`, `category="ecology"`를 사용한다.
- 겹치는 SHP가 하나도 없거나 최종 feature가 0개이면 해당 레이어 실패로 보고한다.
- source record는 streaming하고 최종 clipped feature만 메모리에 유지한다. 일반적인 노선 buffer AOI를 지원 범위로 하며 전국 전체를 출력하는 대형 AOI는 현재 in-memory `SpatialLayer`와 exporter 계약상 지원하지 않는다.

## 공급자 라우팅

현재 `ProjectService`는 모든 선택 레이어를 하나의 `VWorldClient`로 처리한다. 이를 원래 선택 순서를 유지하는 per-definition loop에서 `LayerDefinition.provider` 기준으로 지연 분기한다.

- `vworld`: 첫 VWorld 레이어에서만 기존 `VWorldClient`를 만들고 재사용한다. 로컬 레이어만 선택한 실행은 VWorld API Key를 요구하지 않는다.
- `econatr`: 첫 생태자연도 레이어에서만 `ProjectConfig.econatr_directory`를 사용한 `EconatrProvider`를 만들고 재사용한다.
- 알 수 없는 provider와 provider 생성 실패는 해당 레이어의 `DownloadResult(status="failed")`로 격리한다.
- 생성한 closeable provider는 모두 `finally`에서 닫는다.

기존 테스트가 주입하는 단일 provider는 그대로 전체 레이어에 적용하여 테스트 호환성을 유지한다.

## 출력과 프로젝트 구조

- GeoPackage layer: `econatr`
- Shapefile: `shp/ecology/econatr.shp`
- QGIS group: `06_환경·생태`
- 기본 심볼: 반투명 녹색 polygon. 공식 등급별 필드명은 실제 ZIP 스키마가 확인되기 전까지 추정하지 않는다.
- manifest layer 항목에 routing provider, source URL, publisher, dataset ID, license name/link와 실제 feature에 기여한 ZIP basename만 기록한다.
- 보고서와 로그에는 로컬 절대 경로 전체를 기록하지 않는다.

## 오류 계약

다음 오류는 `ProviderError`로 해당 레이어만 실패시킨다.

- 관리 폴더 없음
- `ECONATR_*.zip` 없음
- 필수 sidecar 누락
- 손상된 ZIP/SHP/DBF
- 지원하지 않는 DBF 인코딩
- `.prj` 누락 또는 EPSG:5186 불일치
- 비 polygon geometry
- AOI와 겹치는 데이터 없음
- 잘못된 geometry를 복구할 수 없음
- ZIP path traversal·중복 member·암호화 entry·크기 제한 초과
- SHP header code/version/type/bbox/길이 오류

오류 메시지는 사용자가 폴더 또는 ZIP을 바로 수정할 수 있도록 파일명과 원인을 한국어로 표시한다.

## 검증

### 자동 테스트

- 최소 polygon, 한글 CP949 속성, EPSG:5186 PRJ가 포함된 작은 ZIP fixture를 테스트 중 생성한다.
- ZIP 다중 SHP 발견과 bbox 선별
- path traversal, 정규화 후 member 충돌, 암호화 entry, size limit
- 잘못된 CRS이면서 AOI와 겹치지 않는 SHP도 반드시 오류 처리
- Polygon→MultiPolygon 승격
- hole·multipart·GeometryCollection을 포함한 invalid geometry 복구
- cross-ZIP 중복 제거
- 실제 AOI intersection 절단과 boundary-only 결과 제거
- `.cpg` 인코딩, CPG 없는 fallback, DBF 날짜 정규화
- 누락 sidecar, 잘못된 CRS, 손상 ZIP, 빈 결과의 격리
- catalog label/search/sort와 체크 상태 유지
- VWorld Key가 없는 local-only 실행, mixed-provider 생성 실패, injected-provider override
- provider별 라우팅과 close
- GPKG/SHP/QGZ/manifest attribution 통합 출력
- 기존 VWorld 전체 회귀 테스트
- `tests/fixtures/build_econatr_fixture.py`가 수동 QGIS QA용 안전한 작은 fixture 폴더를 생성한다.

### 수동 QGIS 검증

1. 전역 관리 폴더를 선택한다.
2. `[환경·생태] 생태자연도`가 목록에 표시되는지 확인한다.
3. fixture generator가 만든 ZIP 또는 사용자가 제공한 공식 ZIP으로 프로젝트를 생성한다.
4. QGIS `06_환경·생태` 그룹에서 clipped polygon과 속성을 확인한다.
5. 원본 ZIP을 제거한 뒤 다시 실행하여 다른 레이어는 유지되고 생태자연도만 경고 처리되는지 확인한다.

## 범위 제외

- VWorld 로그인 및 라온K 자동화
- 공식 ZIP 자동 다운로드
- 백그라운드 폴더 감시 서비스
- 공식 데이터 갱신 알림
- 확인되지 않은 등급 필드 기반 자동 스타일
- 원본 ZIP 재배포
