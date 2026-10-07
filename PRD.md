# RouteGIS AutoBuilder

## 노선도면 기반 QGIS 사업분석 프로젝트 자동 생성 시스템

**문서 버전:** v0.1
**개발 우선순위:** MVP
**실행 환경:** Windows 로컬 PC
**주요 사용자:** 건설사 토목 설계/입찰/기술검토 담당자
**핵심 도구:** QGIS / PyQGIS / GDAL / VWorld OpenAPI

---

# 1. 제품 개요

## 1.1 문제 정의

토목 사업 검토 초기에는 노선도면을 기준으로 QGIS 프로젝트를 새로 구성하고, 노선을 GIS 데이터로 변환한 뒤 사업구간 주변의 여러 공간정보를 반복적으로 수집해야 한다.

현재 일반적인 작업은 다음과 같다.

1. 노선 CAD 도면 확인
2. QGIS 프로젝트 생성
3. 필요한 CAD 레이어 확인
4. 노선 중심선 추출
5. 좌표계 설정
6. SHP 등 GIS 형식으로 변환
7. 사업구간 범위 설정
8. 브이월드에서 지적도·행정경계·용도지역 등 필요한 레이어 조회
9. 데이터를 하나씩 QGIS에 추가
10. 좌표계와 데이터 위치 확인
11. 레이어 정리 및 스타일링
12. 사업분석용 QGIS 프로젝트 저장

사업마다 거의 동일한 작업이 반복되며, GIS에 익숙하지 않은 사용자에게는 좌표계와 공간데이터 수집 과정에서 오류가 발생하기 쉽다.

---

# 2. 제품 목표

사용자가 **노선 도면 하나를 입력하면 사업분석에 바로 사용할 수 있는 QGIS 프로젝트를 자동 생성**한다.

최종 사용자 경험은 다음을 목표로 한다.

> 노선 DWG/DXF 선택
> → 지원 선 객체 전체 분석
> → 좌표계 확인
> → 분석 범위 설정
> → 필요한 공간정보 선택
> → 실행
> → 완성된 QGIS 프로젝트 열기

사용자가 반복적으로 수행하던 CAD 변환, SHP 생성, 공간정보 다운로드, QGIS 레이어 추가 작업을 최대한 제거한다.

---

# 3. 핵심 성공 조건

MVP가 성공했다고 판단하려면 다음 조건을 충족해야 한다.

### SC-01

유효한 DWG/DXF 노선도면과 좌표계 정보를 입력하면 사용자가 별도의 CAD 변환 작업을 하지 않고 노선을 GIS LineString 데이터로 생성할 수 있어야 한다.

### SC-02

변환된 노선을 기준으로 사용자가 지정한 거리의 분석 Buffer/AOI를 생성할 수 있어야 한다.

### SC-03

AOI 범위를 기준으로 선택된 VWorld 공간정보를 자동으로 조회하고 로컬에 저장할 수 있어야 한다.

### SC-04

다운로드 가능한 벡터 데이터는 로컬 데이터로 저장하여 인터넷 연결 없이도 이후 분석할 수 있어야 한다.

### SC-05

최종적으로 자동 생성된 `.qgz` 파일을 QGIS에서 열면 노선과 공간정보 레이어가 정리된 상태로 표시되어야 한다.

### SC-06

모든 결과는 동일한 입력으로 다시 실행했을 때 재현 가능해야 한다.

### SC-07

좌표계가 확실하지 않은 경우 시스템이 임의로 좌표계를 결정해서는 안 된다.

### SC-08

일부 VWorld 레이어 다운로드가 실패해도 전체 작업을 폐기하지 않고 성공한 결과를 보존하고 실패 항목을 보고해야 한다.

---

# 4. 제품 범위

## 4.1 MVP 포함 범위

### 입력

* DWG
* DXF

### 도면 처리

* CAD Layer 조회
* LINE
* POLYLINE
* LWPOLYLINE
* ARC가 포함된 노선의 선형화
* 모든 지원 선 객체 수집
* 객체별 원본 CAD Layer 보존
* GIS LineString 생성

### 좌표계 처리

* 사용자 CRS 선택
* 자주 사용하는 국내 CRS Preset
* 좌표값 기반 CRS 후보 제안
* 좌표변환
* WGS84 변환
* CRS 검증

### 공간범위

노선을 중심으로 다음 방식 지원.

* 일정 거리 Buffer
* Bounding Box
* 사용자 지정 Polygon

기본 워크플로는 `노선 Buffer`로 한다.

### 공간정보

VWorld API를 통해 조회 가능한 주요 사업분석 레이어 다운로드.

### 결과

* GeoPackage
* SHP
* GeoJSON
* QGIS Project (`.qgz`)
* 실행 결과 보고서
* 처리 로그

---

# 5. MVP 제외 범위

다음 기능은 초기 버전에서 구현하지 않는다.

* 종단면 자동 인식
* 횡단면 자동 인식
* PDF 도면 자동 벡터화
* 스캔 도면 OCR
* 도곽 자동 지오리퍼런싱
* BIM 모델 생성
* Civil 3D Alignment 직접 생성
* 자동 토공량 계산
* 자동 설계 검토
* 자동 구조해석
* AI가 도면 전체를 이미지로 읽어 노선을 추론
* 전국 단위 공간정보 일괄 다운로드

PDF 및 이미지 도면 지원은 별도 Phase에서 구현한다.

---

# 6. 제품 형태

## MVP 권장 구조

초기 버전은 **독립 실행 프로그램보다 QGIS Plugin 형태를 우선 구현한다.**

이유는 다음과 같다.

* PyQGIS를 직접 사용 가능
* QGIS의 CRS 기능을 그대로 사용 가능
* QGIS Layer Tree를 직접 제어 가능
* `.qgz` 생성이 단순함
* GIS 데이터 미리보기 기능을 별도로 개발할 필요가 없음
* 독립 앱에 QGIS Runtime을 패키징하는 복잡성을 피할 수 있음

제품 이름은 독립적으로 유지한다.

**RouteGIS AutoBuilder**

QGIS 내부에서는 Plugin 형태로 실행한다.

향후 동일한 Core Engine을 이용해 독립 실행 Desktop UI를 추가한다.

---

# 7. 사용자 Workflow

```text
[새 프로젝트]

       ↓

[DWG / DXF 입력]

       ↓

[CAD 분석]

       ↓

[지원 선 객체 전체 확인]

       ↓

[CRS 확인]

       ↓

[노선 GIS 변환]

       ↓

[분석 범위 생성]

       ↓

[VWorld 레이어 선택]

       ↓

[공간정보 자동 다운로드]

       ↓

[Geometry / CRS 검증]

       ↓

[GeoPackage + SHP 생성]

       ↓

[QGIS Layer Tree 자동 구성]

       ↓

[QGIS Project 저장]

       ↓

[QA Report 출력]
```

---

# 8. 화면 구성

MVP는 Wizard 방식으로 구성한다.

## STEP 1. 프로젝트 생성

입력 항목:

* 사업명
* 프로젝트 저장 위치
* 도면 파일
* VWorld API Key
* 공공데이터포털 서비스키

최근 사용 API Key와 프로젝트 설정을 불러올 수 있어야 한다.

API Key 입력값은 현재 사용자의 QGIS 설정에 저장하되 프로젝트 파일, Manifest, 보고서와 로그에는 저장하지 않는다.

---

# 9. STEP 2. 도면 분석

도면을 열면 CAD Layer 목록을 표시한다.

예:

```text
Layer Name                  Entity       Length
------------------------------------------------
CENTER                      POLYLINE      12.3 km
ALIGN                       POLYLINE      12.2 km
ROAD_EDGE                   POLYLINE      25.8 km
STRUCTURE                   POLYLINE       3.4 km
TEXT                        TEXT              -
DIM                         DIMENSION         -
```

Wizard는 지원되는 모든 `LINE`, `POLYLINE`, `LWPOLYLINE`, `ARC` 객체의 CAD Layer, 객체 유형, 개수와 총 길이를 표시한다. 별도의 사업 유형 질문이나 Layer 선택 없이 모든 유효 객체를 분석 Buffer 기준으로 사용한다.

---

# 10. 노선 Geometry 처리

CAD 객체를 GIS Geometry로 변환한다.

지원 대상:

```text
LINE
POLYLINE
LWPOLYLINE
ARC
```

여러 개의 노선 조각이 존재할 경우 Snap tolerance 기반으로 연결한다.

예:

```text
0.001 m
0.01 m
0.1 m
1.0 m
```

Tolerance는 좌표계 단위에 따라 처리한다.

ARC가 존재하면 지정된 chord tolerance를 이용하여 LineString으로 변환한다.

원본 Geometry와 변환 결과를 모두 보관한다.

---

# 11. 좌표계 처리

좌표계 오류는 이 제품에서 가장 중요한 실패 요인으로 취급한다.

시스템은 좌표계를 임의 확정하지 않는다.

가능한 경우 다음 정보를 이용해 후보를 제시한다.

* Drawing metadata
* Coordinate range
* 프로젝트 Preset
* 이전 프로젝트 설정
* 사용자가 선택한 위치

UI 예:

```text
좌표계 후보

○ EPSG:5186
○ EPSG:5187
○ EPSG:5179
○ EPSG:4326
○ 직접 선택
```

후보 추천은 가능하지만 사용자 확인을 받는다.

좌표변환 이후 자동으로 sanity check를 수행한다.

예:

```text
대한민국 예상 범위 내 위치 여부
경도 124~132도 범위 여부
위도 33~39도 범위 여부
Geometry extent 비정상 여부
```

범위를 벗어나면 경고한다.

---

# 12. 분석 범위 AOI

노선을 기준으로 자동 Buffer를 생성한다.

Preset:

```text
500 m
1 km
2 km
5 km
사용자 지정
```

예:

```text
노선
──────────────

↓ Buffer 1km

████████████████
█ ──────────── █
████████████████
```

각 데이터 레이어는 전체 노선이 아닌 해당 AOI를 기준으로 요청한다.

이를 통해 API 호출량과 데이터 크기를 최소화한다.

---

# 13. VWorld 연동

VWorld Adapter를 별도 모듈로 분리한다.

```text
VWorldClient
 ├─ Authentication
 ├─ LayerCatalog
 ├─ Data API
 ├─ WFS
 ├─ WMS
 ├─ WMTS
 ├─ Pagination
 └─ Retry / Error handling
```

---

# 14. 데이터 수집 원칙

레이어는 다음 우선순위로 가져온다.

```text
1. Vector API
2. WFS
3. 2D Data API
4. WMS
```

벡터 데이터가 제공된다면 WMS 이미지만 가져오지 않는다.

분석 가능한 Geometry와 Attribute를 저장한다.

WMS는 다음 목적에 사용한다.

* 배경정보
* 시각적 확인
* Vector API 미지원 데이터

---

# 15. VWorld Layer Catalog

레이어 ID를 애플리케이션 코드 전체에 Hard Coding하지 않는다.

별도 Catalog를 유지한다.

예:

```yaml
layers:

  cadastral:
    name: 연속지적도
    provider: vworld
    type: wfs
    data_id: ...

  administrative:
    name: 행정경계
    provider: vworld
    type: data
    data_id: ...

  landuse:
    name: 용도지역지구
    provider: vworld
    type: wfs
    data_id: ...
```

VWorld 서비스 변경에 대응할 수 있도록 Layer Catalog와 프로그램 Logic을 분리한다.

---

# 16. 사업분석 Layer Profile

레이어를 하나씩 선택하는 방식 외에 Profile 개념을 지원한다.

초기 Profile:

## 토목 노선 기본

카테고리 예시:

```text
[필지 / 토지]
연속지적도
토지 관련 기본정보

[행정]
시도
시군구
읍면동

[토지이용]
용도지역
용도지구
용도구역

[시설]
도로
건축물

[규제 / 사업성 검토]
개발제한 관련 공간정보
농업 관련 공간정보
보전 관련 공간정보
```

실제 데이터 ID는 VWorld Layer Catalog를 기준으로 정의한다.

사용자는 체크박스로 추가/삭제할 수 있다.

---

# 17. 향후 Layer Profile

향후 다음 Preset을 제공한다.

```text
도로사업
철도사업
터널사업
교량사업
단지사업
산업단지
도심지 지하공사
```

예를 들어 터널사업 Profile에는 향후 다음 데이터 Source를 연계할 수 있다.

* 지질
* 산사태
* 단층
* 지하수
* 광산
* 지하안전
* 보호구역
* 지형고도
* 토지피복

VWorld 외 데이터는 향후 별도 Adapter로 추가한다.

---

# 18. API Pagination

VWorld에서 한 번에 모든 데이터를 반환한다고 가정하지 않는다.

Data Fetcher는 자동 Pagination을 구현한다.

```text
AOI

 ↓

Request Page 1
 ↓
Request Page 2
 ↓
Request Page 3
 ↓
...

 ↓

Merge

 ↓

Geometry validation
```

동일 Feature 중복 여부를 확인하고 제거한다.

---

# 19. 대규모 AOI 대응

AOI가 너무 큰 경우 자동 Tile 분할 기능을 사용한다.

예:

```text
┌────┬────┬────┐
│ 01 │ 02 │ 03 │
├────┼────┼────┤
│ 04 │ 05 │ 06 │
├────┼────┼────┤
│ 07 │ 08 │ 09 │
└────┴────┴────┘
```

각 Grid별로 API를 호출한 뒤 결과를 Merge한다.

중복 Feature는 제거한다.

---

# 20. 로컬 데이터 저장 구조

Shapefile만을 내부 기본 포맷으로 사용하지 않는다.

**GeoPackage를 Primary Storage로 사용하고 SHP는 호환용 Export로 제공한다.**

이유:

* 여러 레이어를 파일 하나에 저장 가능
* 한글 필드 및 Encoding 문제 감소
* 긴 Field Name 지원
* 공간 Index 지원
* QGIS 호환성이 좋음

---

# 21. 결과 폴더 구조

```text
PROJECT_NAME/

├─ project/
│   └─ PROJECT_NAME.qgz
│
├─ source/
│   └─ route_original.dwg
│
├─ data/
│   ├─ project.gpkg
│   └─ raw/
│
├─ shp/
│   ├─ route/
│   ├─ cadastral/
│   ├─ landuse/
│   └─ administrative/
│
├─ style/
│   └─ *.qml
│
├─ reports/
│   ├─ processing_report.html
│   └─ processing_report.json
│
├─ logs/
│   └─ app.log
│
└─ project_manifest.json
```

---

# 22. GeoPackage 구조

예:

```text
project.gpkg

route_centerline
route_buffer

cadastral
administrative_sido
administrative_sigungu
administrative_emd

landuse_zone
landuse_district

road
building
```

레이어명은 영문 내부 이름과 한글 Display Name을 분리한다.

---

# 23. SHP Export

사용자가 SHP 출력 옵션을 활성화하면 각 레이어를 별도 SHP로 저장한다.

Shapefile 제약을 처리한다.

* 필드명 길이 제한
* Encoding
* Geometry Type
* Null handling

원래 Attribute 이름과 SHP Attribute 이름의 대응표를 저장한다.

예:

```json
{
  "landuse_zone": {
    "original_field_name": "land_use_category_name",
    "shp_field_name": "landuse_nm"
  }
}
```

---

# 24. QGIS Project 자동 생성

QGIS 프로젝트의 Layer Tree를 자동으로 만든다.

예:

```text
PROJECT_NAME

├─ 00_BASE
│   ├─ VWorld Base Map
│   └─ VWorld Satellite
│
├─ 01_ROUTE
│   ├─ Route Centerline
│   └─ Analysis Buffer
│
├─ 02_CADASTRAL
│   └─ Cadastral
│
├─ 03_LANDUSE
│   ├─ Zone
│   ├─ District
│   └─ Area
│
├─ 04_ADMIN
│   ├─ Sido
│   ├─ Sigungu
│   └─ EMD
│
├─ 05_FACILITY
│   ├─ Road
│   └─ Building
│
└─ 99_SOURCE
    └─ Original CAD
```

---

# 25. 자동 Styling

기본 QML Style을 적용한다.

예:

### 노선

```text
Red
2.0 px
Solid
```

### 분석 Buffer

```text
Transparent fill
Red outline
```

### 지적도

```text
Transparent fill
Gray outline
```

### 용도지역

Category 기반 색상 분류를 지원한다.

Style은 프로그램 코드와 분리하여 `.qml` 파일로 관리한다.

---

# 26. 자동 Zoom

프로젝트를 처음 열면 자동으로 노선 전체 범위로 Zoom한다.

노선이 없는 경우 AOI 범위로 Zoom한다.

---

# 27. Processing Report

작업 완료 후 HTML Report를 생성한다.

예:

```text
PROJECT
OO 고속도로 기본설계

SOURCE
route.dwg

ROUTE
Length : 18.23 km

CRS
EPSG:5186

AOI
Buffer : 1,000 m
Area : 42.1 km²

DOWNLOADED DATA

연속지적도          SUCCESS     12,403 Features
행정경계            SUCCESS         18 Features
용도지역            SUCCESS        421 Features
건물                SUCCESS      8,321 Features
도로                 FAILED

FAILED
도로 API timeout

OUTPUT
project.gpkg
project.qgz
```

---

# 28. QA 자동 검증

완료 전에 다음 Validation을 실행한다.

## Geometry

* Empty geometry
* Invalid geometry
* Duplicate geometry
* Geometry type
* Self intersection

## CRS

* CRS 존재 여부
* 좌표변환 성공 여부
* 대한민국 영역 내 위치 여부

## Route

* Length > 0
* 단절 구간 확인
* AOI 생성 여부

## VWorld

* API 응답 성공 여부
* Feature count
* Empty result
* Pagination 정상 종료

---

# 29. 좌표계 오류 탐지

다음 상황은 Critical Warning으로 처리한다.

```text
노선 위치: 서울 예상

VWorld 데이터 위치: 동해상

→ CRS mismatch 가능성
```

노선 Bounding Box와 다운로드 데이터 Bounding Box의 공간적 관계를 확인한다.

전혀 교차하지 않으면 결과를 성공 처리하지 않는다.

---

# 30. 실패 처리 원칙

하나의 Layer 다운로드 실패로 전체 프로젝트 생성을 실패시키지 않는다.

예:

```text
Cadastral       SUCCESS
Administrative  SUCCESS
Landuse         SUCCESS
Building        FAILED
Road            SUCCESS
```

프로젝트는 생성하되 다음 상태로 표시한다.

```text
COMPLETED_WITH_WARNINGS
```

---

# 31. VWorld API 장애 대응

지원해야 할 상황:

* API Key 없음
* API Key 오류
* HTTP Timeout
* Rate Limit
* 빈 응답
* Invalid JSON
* Pagination 오류
* 특정 레이어 일시 장애

일시적인 오류에는 제한된 횟수의 Retry와 Backoff를 적용한다.

무한 Retry는 금지한다.

---

# 32. 보안

본 프로그램은 기업 내부 도면을 다룰 수 있으므로 Local-first를 원칙으로 한다.

### 원칙

CAD 원본을 외부 AI API로 전송하지 않는다.

VWorld에는 공간정보 조회에 필요한 범위 정보만 전송한다.

예:

```text
BBOX
Polygon
Layer ID
```

API Key는 Log에 기록하지 않는다.

API Key는 Git Repository에 저장하지 않는다.

가능하면 OS Credential Store를 사용한다.

Wizard에서 입력값을 기억할 때는 현재 사용자의 QGIS 설정에만 저장하고 Password 입력란으로 마스킹한다. 사용자가 입력란을 비우면 저장된 Key도 삭제한다.

---

# 33. AI 사용 정책

MVP Core 기능은 AI 없이 동작해야 한다.

다음 작업은 결정론적 GIS Library가 수행한다.

* CAD Geometry 변환
* 좌표변환
* Buffer
* Spatial filtering
* API 호출
* Geometry validation
* File export

AI는 향후 다음 작업에만 선택적으로 사용할 수 있다.

### AI Assist

CAD Layer 이름이 불명확한 경우 노선 후보 추천.

예:

```text
A-RD-01
R-LN-C
L_001
L_002
```

AI에는 Geometry 자체가 아니라 가능한 최소 정보만 제공한다.

예:

```json
{
  "layers": [
    {
      "name": "R-LN-C",
      "entity": "POLYLINE",
      "length": 18230
    }
  ]
}
```

---

# 34. 기술 구조

```text
┌───────────────────────────────┐
│        QGIS Plugin UI         │
└───────────────┬───────────────┘
                │
┌───────────────▼───────────────┐
│         Application Core      │
│                               │
│ ProjectService                │
│ RouteService                  │
│ AOIService                    │
│ LayerService                  │
└───────────────┬───────────────┘
                │
      ┌─────────┴─────────┐
      │                   │
┌─────▼─────┐       ┌─────▼─────┐
│ CAD Core  │       │ GIS Core  │
│           │       │           │
│ ezdxf     │       │ PyQGIS    │
│ DWG       │       │ GDAL/OGR  │
└─────┬─────┘       │ PROJ      │
      │             └─────┬─────┘
      │                   │
      └─────────┬─────────┘
                │
       ┌────────▼────────┐
       │ Data Providers  │
       │                 │
       │ VWorld Adapter  │
       │ Future Adapters │
       └─────────────────┘
```

---

# 35. 권장 기술 Stack

## Language

Python

이유:

* QGIS Python API
* GDAL
* ezdxf
* pyproj
* shapely
* geopandas 등 GIS 생태계 활용 가능

단 QGIS 설치환경과 Python 패키지 호환성을 우선한다.

---

# 36. 주요 Library

### QGIS

```text
PyQGIS
```

용도:

* Project 생성
* Layer 생성
* Layer Tree
* CRS
* Style
* QGIS UI 연동

### CAD

```text
ezdxf
```

용도:

* DXF 분석
* CAD Layer
* Entity
* Polyline

DWG 파일은 직접 파싱 Library를 프로그램 내부에 무리하게 구현하지 않는다.

설정된 DWG → DXF Converter를 호출하는 Adapter 구조로 만든다.

---

# 37. DWG 처리

DWG 처리는 외부 Converter Adapter를 둔다.

```text
DWG

 ↓

CADConverter

 ↓

Temporary DXF

 ↓

DXF Parser
```

프로그램은 특정 상용/외부 변환도구와 Core Logic이 결합되지 않도록 한다.

Converter가 설치되지 않은 경우 사용자가 이해할 수 있는 오류를 출력한다.

---

# 38. GIS Library

필요에 따라 사용:

```text
GDAL / OGR
PROJ
Shapely
PyProj
GeoPandas
```

다만 동일 기능을 PyQGIS에서 안정적으로 제공하면 PyQGIS를 우선한다.

Dependency를 불필요하게 중복시키지 않는다.

---

# 39. API Client

```text
httpx
```

또는 이에 준하는 HTTP Client.

요구 기능:

* Timeout
* Retry
* Query Parameter
* JSON
* Streaming
* Connection reuse

---

# 40. 설정 관리

```text
config.yaml
```

예:

```yaml
project:

  default_buffer: 1000

vworld:

  timeout: 30
  retry: 3

cad:

  snap_tolerance: 0.1

export:

  geopackage: true
  shapefile: true
```

---

# 41. Manifest

실행 결과를 `project_manifest.json`으로 기록한다.

예:

```json
{
  "project": "Sample Project",

  "source": {
    "filename": "route.dwg"
  },

  "route": {
    "crs": "EPSG:5186",
    "buffer": 1000
  },

  "layers": [
    {
      "name": "cadastral",
      "status": "success",
      "features": 12403
    }
  ]
}
```

이 파일을 통해 언제 어떤 설정으로 프로젝트가 만들어졌는지 재현할 수 있어야 한다.

---

# 42. Logging

다음 Level을 사용한다.

```text
DEBUG
INFO
WARNING
ERROR
```

사용자 화면에는 중요한 메시지만 노출한다.

상세한 오류는 Log에 기록한다.

API Key는 반드시 Masking한다.

---

# 43. 프로젝트 구조

권장 Repository 구조:

```text
routegis-autobuilder/

├─ src/
│   └─ routegis/
│
│       ├─ app/
│       │   ├─ project_service.py
│       │   ├─ route_service.py
│       │   └─ layer_service.py
│       │
│       ├─ cad/
│       │   ├─ reader.py
│       │   ├─ converter.py
│       │   └─ route_detector.py
│       │
│       ├─ gis/
│       │   ├─ crs.py
│       │   ├─ geometry.py
│       │   ├─ export.py
│       │   └─ validator.py
│       │
│       ├─ providers/
│       │   └─ vworld/
│       │       ├─ client.py
│       │       ├─ catalog.py
│       │       └─ downloader.py
│       │
│       ├─ qgis/
│       │   ├─ project.py
│       │   ├─ layers.py
│       │   └─ styles.py
│       │
│       └─ models/
│
├─ plugin/
│
├─ resources/
│   ├─ layer_catalog.yaml
│   ├─ styles/
│   └─ presets/
│
├─ tests/
│   ├─ unit/
│   ├─ integration/
│   └─ fixtures/
│
├─ docs/
│
├─ PRD.md
└─ README.md
```

---

# 44. 핵심 Domain Model

## ProjectConfig

```text
project_name
source_drawing
source_crs
output_crs
buffer_distance
selected_layers
output_directory
```

## RouteGeometry

```text
geometry
source_layer
source_crs
length
bbox
```

## LayerDefinition

```text
id
name
provider
service_type
geometry_type
category
```

## DownloadResult

```text
layer_id
status
feature_count
output
error
```

---

# 45. 상태 관리

프로젝트 전체 상태:

```text
NEW

CAD_LOADED

ROUTE_SELECTED

CRS_CONFIRMED

AOI_CREATED

DOWNLOADING

VALIDATING

EXPORTING

COMPLETED

COMPLETED_WITH_WARNINGS

FAILED
```

---

# 46. UX 원칙

GIS 비전문가도 사용할 수 있어야 한다.

따라서 사용자가 다음 용어를 모두 알아야 사용할 수 있는 UI를 만들지 않는다.

예:

```text
WFS
GetFeature
TYPENAME
BBOX
EPSG
OGC
```

UI에서는 가능하면 다음과 같이 표현한다.

```text
노선 좌표계

사업구간 주변 범위

가져올 공간정보

다운로드
```

고급 옵션에서만 기술적인 값을 노출한다.

---

# 47. 오류 메시지

나쁜 예:

```text
OGR ERROR 6
```

좋은 예:

```text
노선 좌표계를 확인할 수 없습니다.

현재 도면의 좌표값만으로 좌표계를 확정할 수 없습니다.
프로젝트에서 사용하는 좌표계를 선택해 주세요.
```

---

# 48. 테스트 전략

테스트는 구현 자체를 반복 확인하기 위한 형식적인 테스트가 아니라 실제 실패 가능성이 높은 부분을 검증한다.

## Unit Test

핵심 대상:

```text
DXF Geometry extraction
Polyline merge
CRS transform
Buffer generation
API pagination
Feature merge
Geometry validation
Layer catalog
```

---

# 49. Integration Test

VWorld API Client에 대해 다음 테스트를 한다.

```text
정상 요청
빈 결과
Pagination
Timeout
Retry
Invalid Key
잘못된 Layer
```

CI에서는 가능하면 Mock Response를 이용한다.

실제 API를 사용하는 테스트는 별도로 구분한다.

---

# 50. Golden Fixture

테스트용 간단한 노선도면을 Repository에 포함한다.

```text
sample_route.dxf
```

Expected 결과:

```text
route length
bbox
buffer bbox
geometry type
feature count
```

이를 Golden Fixture로 사용한다.

---

# 51. End-to-End Test

최소 하나의 테스트 프로젝트를 대상으로 다음 결과를 검증한다.

```text
DXF 입력

→ Route 생성

→ Buffer 생성

→ Mock VWorld 데이터 수집

→ GeoPackage 생성

→ QGIS Project 생성
```

결과 `.qgz`가 실제 QGIS에서 정상적으로 열리는지 검증한다.

---

# 52. MVP Acceptance Test

다음 Workflow가 성공해야 MVP Complete로 판단한다.

### Given

좌표계가 알려진 노선 DWG 또는 DXF가 있다.

### When

사용자가

```text
파일 선택
→ 노선 선택
→ 좌표계 선택
→ Buffer 1km
→ 기본 공간정보 Profile
→ 실행
```

을 수행한다.

### Then

다음 파일이 생성된다.

```text
PROJECT.qgz
project.gpkg
route.shp
VWorld layer SHP files
processing_report.html
project_manifest.json
```

QGIS 프로젝트를 열면 다음 상태여야 한다.

```text
노선 정상 표시
Buffer 정상 표시
VWorld 데이터 정상 위치
레이어 Group 구성
Style 적용
노선 범위 Zoom
```

---

# 53. Performance 기준

MVP에서는 절대적인 처리시간보다 다음을 우선한다.

1. 정확성
2. 데이터 누락 방지
3. 좌표계 오류 방지
4. 재현성
5. 실패 복구
6. 처리속도

단 동일 API에 대한 불필요한 반복 요청은 하지 않는다.

---

# 54. Cache

VWorld 데이터는 프로젝트별 Local Cache를 둘 수 있다.

동일한 요청이 존재하면 다시 다운로드하지 않는다.

Cache Key 예:

```text
Provider
Layer
AOI
CRS
Data version
```

사용자가 강제 Refresh를 선택할 수 있어야 한다.

---

# 55. 향후 확장 Architecture

공간정보 공급자는 Adapter 방식으로 설계한다.

```text
SpatialDataProvider

 ├─ VWorldProvider
 ├─ NGIIProvider
 ├─ KICTProvider
 ├─ KMAProvider
 ├─ KGSProvider
 ├─ MOLITProvider
 └─ CompanyInternalProvider
```

Core Logic이 VWorld에 종속되지 않도록 한다.

---

# 56. Phase 2

MVP 이후 우선 기능:

### 56.1 자동 사업분석

노선과 공간정보를 단순 표시하는 것을 넘어 Spatial Intersection을 자동 수행한다.

예:

```text
노선 Buffer ∩ 개발제한구역

노선 ∩ 행정구역

노선 ∩ 용도지역

노선 ∩ 필지
```

---

# 57. 사업분석 Report

예:

```text
전체 노선길이
18.2 km

서울시
3.4 km

경기도
14.8 km

개발제한구역 통과
2.3 km

농업진흥지역 통과
0.8 km

영향 필지
342 필지
```

이를 Excel/HTML Report로 출력한다.

---

# 58. Phase 3 — 지형 분석

DEM 연계 후 다음 기능을 추가한다.

```text
노선 종단 지형 추출

평균 경사

최대 경사

고도 변화

계곡부 탐지

산지 구간 탐지
```

---

# 59. Phase 4 — 설계 Risk 분석

기존 공간정보 및 회사 데이터를 결합한다.

예:

```text
Route

+

Terrain

+

Land Use

+

Geology

+

Groundwater

+

Existing Structures

+

Historical Design Changes

↓

Project Risk Features
```

이를 향후 입찰/설계변경 Risk Modeling 시스템의 Feature 생성 Pipeline으로 사용할 수 있게 한다.

---

# 60. 개발 우선순위

## P0

반드시 구현:

```text
DXF 읽기
DWG 변환 Adapter
CAD Layer 조회

노선 선택
노선 Geometry 변환

CRS 설정
좌표변환

Buffer 생성

VWorld API Client
Layer Catalog

GeoPackage 저장
SHP Export

QGIS Project 자동 생성

Validation
Processing Report
```

## P1

MVP 이후:

```text
노선 자동 탐색

다운로드 Cache

대규모 AOI Tile 처리

Layer Profile

자동 Styling

Failure Retry
```

## P2

향후:

```text
AI Layer 추천
PDF 도면
자동 CRS 추론
사업분석
공간통계
Excel Report
```

---

# 61. 가장 먼저 만들 Vertical Slice

전체 기능을 각각 따로 개발한 뒤 연결하지 않는다.

우선 다음 End-to-End Workflow 하나를 완성한다.

```text
sample_route.dxf

↓

CENTER Layer 선택

↓

EPSG 직접 지정

↓

LineString 생성

↓

1km Buffer

↓

VWorld 지적도 1개 조회

↓

project.gpkg

↓

sample_project.qgz
```

이 Pipeline이 실제 QGIS에서 작동하면 이후 레이어와 자동화를 확장한다.

---

# 62. 개발 완료 정의

기능 구현만으로 완료 처리하지 않는다.

각 Feature는 다음 조건을 만족해야 한다.

```text
기능 구현

+

정상 경로 테스트

+

대표 실패 경로 테스트

+

실제 QGIS에서 결과 확인

+

사용자에게 오류가 이해 가능한 형태로 표시

+

Documentation 업데이트
```

---

# 63. GPT-5.6 Sol 개발 에이전트 지침

이 PRD를 기반으로 개발하는 AI Agent는 다음 원칙을 따른다.

## 목표

작동하는 RouteGIS AutoBuilder MVP를 완성한다.

계획이나 코드 예시만 제시하는 것으로 작업을 완료했다고 판단하지 않는다.

사용자가 구현을 요청한 경우 Repository를 직접 분석하고 필요한 코드를 작성하고 검증한다.

---

## 성공 조건

작업 완료는 다음 상태를 의미한다.

```text
요청한 기능 구현 완료

관련 기존 기능 손상 없음

적절한 테스트 통과

Lint / syntax 오류 없음

실제 실행 가능한 상태

중요한 제한사항이 문서화됨
```

---

## 구현 방법

PRD에서 구현 방법을 명시하지 않은 부분은 현재 코드베이스와 사용 Library를 분석해 가장 단순하고 유지보수하기 쉬운 방법을 선택한다.

불필요한 Framework나 추상화 Layer를 추가하지 않는다.

새로운 Architecture가 기존 Architecture보다 명확한 장점이 없으면 기존 구조를 유지한다.

---

## 자율 실행 범위

다음 작업은 별도 확인 없이 수행할 수 있다.

```text
Repository 탐색
코드 수정
새 파일 생성
Test 작성
Test 실행
Lint 실행
Fixture 생성
Documentation 수정
Refactoring
로컬 임시 파일 생성
```

다음 작업은 실행 전에 사용자 확인이 필요하다.

```text
System-wide Software 설치
QGIS 자체 설치/제거
외부 서비스 유료 사용
Credential 변경
실데이터 삭제
Git history 파괴
Production 배포
```

---

## 질문 기준

사소한 구현 선택 때문에 작업을 중단하지 않는다.

합리적인 가정을 할 수 있는 경우 가정을 명시하고 진행한다.

다음 경우에만 질문한다.

```text
결과 자체가 달라지는 요구사항 충돌

필수 Credential 없음

사용자 데이터 손실 가능성

둘 이상의 선택지가 제품 구조를 크게 바꾸는 경우
```

그 외에는 가장 안전하고 단순한 선택으로 구현한다.

---

## GIS 계산 원칙

정확한 계산이 가능한 작업을 LLM 추론으로 대체하지 않는다.

다음 작업은 GIS Library를 사용한다.

```text
좌표변환
거리
면적
Buffer
Intersection
Geometry validation
Projection
Feature filtering
```

AI가 좌표값을 직접 계산하거나 추정하지 않는다.

---

## CRS 원칙

CRS를 확실하게 식별할 근거가 없다면 자동 확정하지 않는다.

사용자에게 후보는 제시할 수 있다.

최종 CRS가 명확하지 않으면 해당 단계에서 사용자가 확인하도록 한다.

잘못된 CRS로 정상 완료된 것처럼 처리하는 것보다 작업을 중단하는 것이 우선이다.

---

## 외부 API 원칙

VWorld API 응답은 항상 검증한다.

다음을 가정하지 않는다.

```text
항상 HTTP 200
항상 JSON
항상 Feature 존재
한 페이지에 모든 데이터 존재
Geometry가 항상 Valid
Layer ID가 영구적으로 유지
```

---

## 보안 원칙

Credential을 코드에 Hard Coding하지 않는다.

다음을 Log에 기록하지 않는다.

```text
VWorld API Key
향후 추가되는 Credential
민감한 내부 경로 정보
```

원본 회사 CAD 파일을 외부 AI 서비스로 업로드하지 않는다.

---

## 검증 원칙

변경 범위에 필요한 테스트를 실행한다.

작은 변경 후 전체 Test Suite를 반복적으로 실행하지 않는다.

관련 Test가 통과하고 추가적인 실패 징후가 없다면 다음 작업으로 진행한다.

---

## 작업 종료 조건

다음 질문에 모두 YES인 경우 작업을 종료한다.

```text
사용자가 요청한 실제 결과가 만들어졌는가?

코드가 실행 가능한가?

핵심 Failure Case가 처리되는가?

테스트 결과가 확인되었는가?

남은 Blocker가 사용자 행동 없이는 해결 불가능한가?
```

그렇지 않다면 가능한 작업을 계속 수행한다.

---

# 64. MVP 최종 사용자 경험

최종 목표 UI:

```text
┌──────────────────────────────────────┐
│ RouteGIS AutoBuilder                 │
├──────────────────────────────────────┤
│                                      │
│ 노선도면                             │
│ C:\Project\Route.dwg       [찾기]    │
│                                      │
│ 노선                                 │
│ CENTER                     [미리보기]│
│                                      │
│ 좌표계                               │
│ EPSG:5186                  [변경]     │
│                                      │
│ 분석범위                             │
│ ● 500m                               │
│ ○ 1km                                │
│ ○ 2km                                │
│ ○ 사용자 지정                       │
│                                      │
│ 공간정보                             │
│ ☑ 지적                              │
│ ☑ 행정경계                          │
│ ☑ 용도지역                          │
│ ☑ 도로                              │
│ ☑ 건축물                            │
│                                      │
│               [프로젝트 생성]        │
└──────────────────────────────────────┘
```

실행 후:

```text
✓ 노선 변환

✓ 분석범위 생성

✓ 지적 데이터 다운로드

✓ 행정경계 다운로드

✓ 용도지역 다운로드

✓ 도로 데이터 다운로드

✓ 건축물 다운로드

✓ GeoPackage 생성

✓ SHP 생성

✓ QGIS 프로젝트 생성

완료

[QGIS에서 열기]
```

사용자는 이 버튼 하나를 누른 뒤 바로 사업분석을 시작할 수 있어야 한다.

---

# 65. 제품의 최종 방향

이 제품은 단순한 `CAD → SHP Converter`를 목표로 하지 않는다.

장기적으로 다음 Pipeline의 첫 단계가 된다.

```text
사업 노선도면

        ↓

공간정보 자동 구축

        ↓

노선 주변 환경 자동 분석

        ↓

규제 / 지형 / 토지 / 시설 분석

        ↓

사업 Risk Feature 생성

        ↓

과거 프로젝트 DB 결합

        ↓

입찰 Risk / 설계변경 Risk 분석
```

따라서 MVP부터 다음 원칙을 유지한다.

**Local-first**

**Deterministic GIS**

**Reproducible Pipeline**

**Provider Adapter**

**GeoPackage-first**

**QGIS-native**

**AI는 보조수단이며 GIS 계산의 대체수단으로 사용하지 않는다.**
