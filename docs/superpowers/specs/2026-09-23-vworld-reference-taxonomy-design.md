# VWorld WMS/WFS 레퍼런스 분류·동의어 검색 설계

## 문제

현재 Wizard는 VWorld WFS `GetCapabilities`에서 187개 `FeatureType`을 실시간으로 불러오지만, 검색 대상은 표시명과 RouteGIS/WFS ID뿐이다. 공식 레퍼런스의 분야 정보는 `GetCapabilities`에 포함되지 않으며 검색어 동의어도 처리하지 않는다.

따라서 공식 레이어명이 `국가유산 지정/보호구역`인 경우 `문화유산` 또는 `문화재` 검색으로 찾을 수 없고, `박물관미술관`, `전통사찰보존`은 이름에 `국가유산`도 들어 있지 않아 같은 분야의 데이터로 묶이지 않는다.

## 목표

- 실시간 WFS 목록 조회와 다운로드 흐름은 그대로 유지한다.
- VWorld 공식 WMS/WFS API 2.0 레퍼런스의 분야·공식명·WFS ID를 검색 메타데이터로 결합한다.
- 표시명, WFS ID, 분야, 분야 동의어로 검색할 수 있게 한다.
- `문화재`, `문화유산`, `국가유산` 검색 시 국가유산 분야 3종이 모두 검색되게 한다.
- 레퍼런스에 아직 없는 신규 WFS ID가 나타나도 목록 조회와 선택은 계속 동작한다.

## 제외 범위

- VWorld 레퍼런스 HTML의 실행 시점 스크래핑
- 국가유산청의 별도 WMS/WFS 및 Open API 연결
- 레이어 다운로드 방식, Geometry 판별, Pagination 변경
- 자동 분류 추론 또는 AI 기반 동의어 생성

## 데이터 설계

### 정적 공식 분류 리소스

`src/routegis/resources/vworld_wfs_taxonomy.yaml`을 추가한다. 이 파일은 공식 레퍼런스 페이지를 기준으로 검토한 버전형 메타데이터이며, 실행 시 네트워크로 레퍼런스 HTML을 읽지 않는다.

```yaml
schema_version: 1
source_url: https://www.vworld.kr/dev/v4dv_wmsguide2_s001.do
retrieved_at: "2026-09-23"
source_logical_service_count: 169
expanded_wfs_id_count: 174
categories:
  국가유산:
    aliases:
      - 문화유산
      - 문화재
  경계:
    aliases: []
layers:
  lt_c_uo301:
    name: 국가유산 지정/보호구역
    category: 국가유산
  lt_p_dgmuseumart:
    name: 박물관미술관
    category: 국가유산
  lt_c_uo501:
    name: 전통사찰보존
    category: 국가유산
```

- `layers` 키는 대소문자를 무시한 실제 WFS `data_id`로 조회한다.
- 공식 레퍼런스 한 행에 WFS ID가 여러 개면 각 ID를 별도 항목으로 확장한다.
- 리소스에는 레퍼런스에 기재된 전체 WFS ID와 분야를 기록한다.
- 공식 문서의 169종은 논리 서비스 행 수이며, taxonomy의 확장 WFS ID 수나 실시간 `FeatureType` 187개와 같다고 가정하지 않는다.
- `expanded_wfs_id_count`는 실제 `layers` 항목 수와 일치해야 한다. 갱신할 때 전체 매핑, 날짜와 두 개의 출처 집계값을 함께 검토한다.
- `categories`는 공식 분야 전체를 선언하며 각 분야의 `aliases`는 빈 목록일 수 있다. 국가유산의 동의어를 검색하면 그 분야의 모든 레이어가 검색된다.
- WFS명이 기재된 행은 WFS ID만 사용한다. 공식 표에서 WFS명이 비어 있으나 같은 행의 WMS ID가 현재 WFS `GetCapabilities`에 동일한 제목으로 존재하는 23개 행은 그 실시간 대조가 확인된 ID만 포함한다. 스타일명과 WFS에 존재하지 않는 WMS 전용 ID는 저장하지 않는다.

### 런타임 결합

- `LayerDefinition`과 `GetCapabilities` 파서는 변경하지 않는다. taxonomy의 분야는 다운로드·출력 그룹에 쓰는 기존 `LayerDefinition.category`와 별개의 `reference_category`다.
- `VWorldCatalog`가 기본 레이어 YAML과 별도로 taxonomy 리소스를 한 번 로드하고, 불변 `TaxonomyEntry(official_name, reference_category)` sidecar map으로 보유한다.
- 검색과 Wizard는 `VWorldCatalog.taxonomy_for(definition)`과 `VWorldCatalog.matches(definition, query)`를 사용한다. taxonomy가 `LayerDefinition`의 어떤 필드도 변경해서는 안 된다.
- taxonomy 조회는 `definition.data_id.casefold()`를 키로 사용한다. RouteGIS 내부 ID가 다른 기본 레이어도 실제 WFS ID로 같은 메타데이터를 찾는다.
- 실시간 목록에만 있고 taxonomy에 없는 ID는 미분류 상태로 유지한다. 오류로 처리하거나 목록에서 제외하지 않는다.
- taxonomy는 기본·실시간 목록에 이미 존재하는 레이어만 보강하며 `LayerDefinition`을 새로 만들지 않는다. taxonomy에만 있는 ID는 표시·선택·다운로드 대상이 아니다.
- 활성 목록은 기본 카탈로그와 실시간 `FeatureType`의 대소문자 무시 `data_id` 합집합이다. 분류된 목록은 그 활성 목록과 taxonomy ID의 교집합이다.
- 화면 표시명은 병합 후 유지된 `LayerDefinition.name`을 사용한다. 기본 YAML과 같은 `data_id`의 실시간 항목은 기존 YAML 표시명을 유지하고, 실시간으로만 발견된 항목은 `GetCapabilities` 제목을 사용한다. taxonomy 공식명은 검색에만 추가한다.

### 리소스 로딩 계약

- 기본 taxonomy 경로는 기존 `resource_root() / "vworld_wfs_taxonomy.yaml"`이며 현재 작업 디렉터리에 의존하지 않는다.
- `VWorldCatalog`는 기본 catalog 경로와 별개로 keyword-only `taxonomy_path`를 받아 테스트 fixture를 주입할 수 있다.
- 로더는 파일·YAML 오류, 필수 키 누락, 잘못된 타입, 빈 이름·분야·동의어, `categories`에 없는 분야 참조, 중복 키와 `casefold()` ID 충돌, provenance 집계 불일치를 `ConfigurationError`로 변환한다.
- taxonomy 로딩 실패는 필수 패키지 리소스 손상으로 처리한다. 플러그인 실행 진입점은 taxonomy 파일명과 `ConfigurationError`의 첫 문장만 포함한 critical dialog를 표시하고 Wizard를 열지 않는다. traceback, API Key와 환경정보는 표시하지 않는다.

## 검색 동작

검색 문자열은 기존처럼 앞뒤 공백을 제거하고 대소문자를 무시한다. 다음 필드를 모두 부분일치 검색한다.

1. 실시간 또는 기본 `LayerDefinition.name`
2. RouteGIS 내부 ID
3. 실제 WFS `data_id`
4. taxonomy의 공식명
5. taxonomy의 분야명
6. 해당 분야의 모든 동의어

동의어는 전역 검색어 치환이 아니라 taxonomy가 해당 분야로 분류한 레이어에 추가되는 검색 필드다. 동의어에도 다른 필드와 동일한 부분일치를 적용한다.

예상 결과:

| 검색어 | 필수 포함 결과 |
|---|---|
| `문화재` | 국가유산 분야 3종 |
| `문화유산` | 국가유산 분야 3종 |
| `국가유산` | 국가유산 분야 3종 |
| `박물관` | `lt_p_dgmuseumart` |
| `lt_c_uo301` | `lt_c_uo301` |
| `개발제한구역` | 기존 결과 유지 |
| `단층` | 기존 결과 유지 |

## Wizard 표시

- 분류된 항목은 `[분야] 표시명` 형식으로 표시한다. 예: `[국가유산] 전통사찰보존`.
- tooltip에는 분야, RouteGIS ID, VWorld ID를 표시한다.
- 정렬 키는 `(taxonomy 없음 여부, reference_category.casefold(), 표시명.casefold(), data_id.casefold(), 내부 ID.casefold())`다.
- 미분류 항목은 `[미분류]`를 화면 앞에 강제로 붙이지 않고 기존 표시명만 사용하며 첫 정렬 키에 따라 마지막에 둔다. 필터링은 목록 순서를 변경하지 않는다.
- 체크 상태는 기존과 동일하게 내부 ID 기준으로 보존한다.
- 상태 라벨의 전체·선택·검색 결과 수 계산은 변경하지 않는다.

## 실패 및 갱신 정책

- taxonomy 파일을 읽을 수 없거나 형식이 잘못되면 설치 오류로 간주해 명확한 `ConfigurationError`를 발생시키고 플러그인 실행 진입점에서 사용자에게 표시한다. 배포 패키지에 포함되는 정적 필수 리소스이므로 조용히 무시하지 않는다.
- taxonomy에 없는 개별 WFS ID는 정상적인 서버 확장으로 간주해 기존 이름·ID 검색만 제공한다.
- `source_url`과 `retrieved_at`을 파일에 기록해 향후 공식 레퍼런스 변경 시 갱신 근거를 남긴다.
- 런타임에 HTML을 스크래핑하거나 taxonomy를 자동 덮어쓰지 않는다.

## 변경 범위

- 추가: `src/routegis/resources/vworld_wfs_taxonomy.yaml`
- 수정: `src/routegis/providers/vworld/catalog.py`
- 수정: `plugin/wizard.py`
- 수정: `plugin/routegis_plugin.py`의 taxonomy 초기화 오류 표시
- 수정: taxonomy/search 관련 단위 테스트
- 수정: `README.md`의 검색 설명
- 패키지 리소스 설정은 현재 YAML 포함 규칙이 taxonomy 파일을 포함하는지 확인하고, 필요할 때만 수정한다.

## 검증

### 자동 테스트

- taxonomy 전체 항목의 WFS ID가 중복되지 않고 분야가 비어 있지 않다.
- provenance의 schema version, URL, ISO 날짜, 논리 서비스 수와 확장 WFS ID 수가 존재하고 내부적으로 일치한다.
- `문화재`, `문화유산`, `국가유산`이 국가유산 3종을 모두 반환한다.
- 표시명, 내부 ID, WFS ID의 기존 검색 테스트가 계속 통과한다.
- taxonomy에 없는 실시간 레이어도 병합·검색·선택할 수 있고 접두사 없이 마지막에 정렬된다.
- taxonomy에만 있고 활성 catalog에 없는 ID는 목록·선택·다운로드 대상으로 생성되지 않는다.
- taxonomy 결합 전후 모든 `LayerDefinition` 필드, 특히 다운로드용 `category`, endpoint, service type, CRS, Geometry가 동일하다.
- 기존 YAML 정의 우선, Data API 기본 레이어 유지, 체크 상태 보존이 계속 통과한다.
- 기본·실시간·taxonomy의 항목 수가 서로 달라도 기본과 실시간 ID 합집합 전체가 유지된다.
- 기본 YAML과 실시간 정의가 같은 `data_id`이면 기존 정의와 표시명이 유지되면서 taxonomy만 결합된다.
- 빈 검색, 공백 제거, 대소문자, 공식명, 분야명, 각 동의어와 alias 범위를 각각 검증한다.
- 정확한 정렬 키와 필터 전후 순서 불변을 검증한다.
- taxonomy 누락·불량 YAML·잘못된 schema·중복 및 대소문자 충돌이 `ConfigurationError`와 사용자-facing 오류로 연결된다.
- wheel에 taxonomy 리소스가 포함되고 설치본에서 로드된다.
- 분류된 live 레이어 다운로드 시 원본 WFS ID와 운영 필드가 그대로 `DescribeFeatureType` 및 다운로드 경로에 전달된다.

### QGIS Smoke Test

- 실시간 목록을 불러온 뒤 `[국가유산]` 표시가 보인다.
- `문화재`, `문화유산`, `국가유산` 각각의 검색 결과에 다음 3개가 포함된다.
  - `lt_c_uo301`
  - `lt_p_dgmuseumart`
  - `lt_c_uo501`
- 갱신 전 체크한 기본 레이어가 갱신과 검색 후에도 체크 상태를 유지한다.
- 국가유산 레이어 선택이 기존 프로젝트 생성 및 Geometry 확인 경로에 전달된다.
- taxonomy에 없는 테스트 레이어가 목록에서 사라지지 않는다.

## 수용 기준

- 사용자가 VWorld의 과거·일반 용어인 `문화재` 또는 `문화유산`을 입력해도 현재 공식 `국가유산` 분야 3종을 찾을 수 있다.
- 현재 확인된 187개를 포함하여 `GetCapabilities`가 반환하는 모든 고유 `FeatureType`이 유지되며, 서버 측 개수 변화 자체를 오류로 간주하지 않는다. 기존 기본 7개와 다운로드 기능에도 회귀가 없다.
- 검색·분류는 공식 레퍼런스의 체크인된 메타데이터에 근거하며 실행 중 HTML 구조에 의존하지 않는다.
