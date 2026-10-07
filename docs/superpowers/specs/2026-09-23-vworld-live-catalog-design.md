# VWorld 실시간 레이어 검색 설계

## 문제

현재 QGIS Wizard의 검색창은 `layer_catalog.yaml`에 정의된 7개 레이어만 필터링한다. 브이월드 WFS `GetCapabilities`가 제공하는 전체 레이어는 조회하지 않으므로, 공식 서비스에 존재하지만 로컬 카탈로그에 없는 `개발제한구역`, `단층` 등의 검색 결과가 0건이다.

## 목표

- 사용자가 API Key를 입력하거나 플러그인 `.env`에 설정한 뒤 브이월드 WFS 전체 목록을 불러올 수 있다.
- 표시명과 실제 WFS 레이어 ID로 검색할 수 있다.
- 검색 결과에서 체크한 레이어가 기존 프로젝트 생성 흐름에서 실제로 다운로드된다.
- 네트워크, 인증 또는 응답 오류가 발생해도 기존 7개 기본 목록은 유지되고 사용자에게 원인이 표시된다.

## 설계

### Core

- `VWorldClient`에 WFS 1.1.0 `GetCapabilities` 조회 기능을 추가한다.
- 기존 JSON 요청과 별도로 XML 응답용 요청 경로를 두되, 기존과 같은 Timeout, 제한된 Retry, API Key 마스킹을 적용한다.
- XML은 표준 라이브러리 `xml.etree.ElementTree`로 파싱한다. VWorld 응답의 namespace 유무에 모두 대응하도록 태그의 local name으로 `FeatureType`, `Name`, `Title`을 찾는다.
- `Title`이 없으면 `Name`을 표시명으로 사용하고, `Name`이 없으면 해당 항목을 제외한다.
- 실시간 정의는 `id=data_id`, `service_type=wfs`, 요청·응답 CRS `EPSG:4326`, 카테고리 `facility`, Geometry 유형 `Unknown`으로 만든다.
- `VWorldClient.fetch_layer()`는 Geometry 유형이 `Unknown`이면 GetFeature보다 먼저 WFS 1.1.0 `DescribeFeatureType`을 호출한다. 응답 스키마의 Geometry 속성 유형을 다음과 같이 변환한다.
  - `PolygonPropertyType`, `MultiSurfacePropertyType`, `SurfacePropertyType`: `MultiPolygon`
  - `LineStringPropertyType`, `CurvePropertyType`, `MultiCurvePropertyType`: `MultiLineString`
  - `PointPropertyType`, `MultiPointPropertyType`: `MultiPoint`
- 확정된 유형은 `dataclasses.replace()`로 만든 새 `LayerDefinition`에 적용하고, 그 정의로 `SpatialLayer`를 생성한다. Frozen 원본 정의와 카탈로그는 변경하지 않는다.
- `DescribeFeatureType` 실패, Geometry 속성 누락 또는 미지원 유형은 해당 레이어의 실패 `DownloadResult`로 격리하며, `Unknown` 상태로 GetFeature나 내보내기를 계속하지 않는다. 접두사 추정은 사용하지 않는다.
- `VWorldCatalog.merge_live()`는 현재 `ProjectService.catalog` 인스턴스를 제자리에서 갱신한다. 기존 YAML 정의와 `data_id`가 같으면 기존 정의를 우선하고, 나머지 실시간 정의를 `id`로 등록한다.
- `ProjectService.build()`의 선택 레이어 검증도 새 YAML을 다시 읽지 않고 동일한 병합 카탈로그를 사용한다. 검색 목록에 보인 실시간 레이어가 빌드 검증에서 거부되지 않아야 한다.
- 현재 7개 기본 항목 중 Data API 전용 행정경계 3개는 WFS 목록에 없더라도 YAML 정의로 계속 유지한다.

### QGIS Wizard

- 공간정보 검색 입력란 옆에 `브이월드 목록 불러오기` 버튼을 둔다.
- 버튼 클릭 시 프로젝트 화면의 임시 API Key 또는 플러그인 `.env` Key와 등록 도메인을 사용한다.
- 성공하면 목록을 갱신하고 현재 검색어를 다시 적용한다.
- 체크 상태는 레이어 내부 ID 기준으로 보존한다.
- 목록은 표시명, WFS ID 순으로 정렬한다.
- 동기 HTTP 호출 중 버튼을 비활성화하고 대기 커서를 표시하며, 완료 후 버튼과 커서를 반드시 복원한다. 호출 시간은 기존 VWorld Timeout 설정으로 제한한다.
- 실패 시 기본 목록을 유지하고 메시지 상자 및 상태 라벨에 원인을 표시한다.

## 검증

- `tests/fixtures/vworld_capabilities.xml`로 namespace 유무, Title 누락, 접두사 비정형 ID를 포함한 목록 파싱 단위 테스트.
- `DescribeFeatureType` XML로 Polygon, LineString, Point 유형을 각각 확정하는 단위 테스트.
- 기존 7개와 실시간 목록 병합 시 기존 Alias, Data API 전용 행정경계, 선택 상태가 유지되는 테스트.
- `ProjectService.build()`가 병합 카탈로그의 실시간 ID를 검증하고 선택하는 통합 테스트.
- QGIS 3.34에서 `개발제한구역`, `단층`, 실제 WFS ID를 검색하고 체크 상태를 보존하는 Smoke Test.
- 잘못된 Key 또는 네트워크 오류에서 기본 7개가 남는 실패 경로 확인.

## 제외 범위

- WMS 전용 레이어
- Geometry 접두사를 판별할 수 없는 레이어
- 자동 주기 갱신과 디스크 캐시
- 브이월드 HTML 페이지 스크래핑
