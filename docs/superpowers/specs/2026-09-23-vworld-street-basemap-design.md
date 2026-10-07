# VWorld Street 기본 레이어 설계

## 목표

RouteGIS가 새로 생성하는 모든 QGIS 프로젝트에 TMS for Korea의 `VWorld Street`를 자동으로 포함한다. 레이어는 QGIS 레이어 패널의 전체 목록 맨 아래에 표시되고 다른 모든 레이어보다 먼저 그려지는 기본 배경 지도가 된다.

## 레이어 정의

- 이름: `VWorld Street`
- QGIS 레이어 유형: XYZ raster
- 공급자 키: `wms`
- 좌표계: `EPSG:3857`
- URL: `https://xdworld.vworld.kr/2d/Base/service/{z}/{x}/{y}.png`
- 확대 수준: `0`~`18`
- 타일 픽셀 비율: `1`
- QGIS datasource URI: `url=https://xdworld.vworld.kr/2d/Base/service/{z}/{x}/{y}.png&zmax=18&zmin=0&type=xyz&tilePixelRatio=1`
- 초기 표시 상태: `Qt::Checked`

위 값은 이 PC에 설치된 TMS for Korea 플러그인의 `OlVWorldStreetLayer` 및 `createXYZLayer()` 정의와 동일하다. 생성된 프로젝트가 해당 플러그인 설치 여부에 의존하지 않도록 표준 `QgsRasterLayer`/QGS XML로 직접 저장한다.

## 프로젝트 생성 동작

`src/routegis/qgis/project.py`의 두 생성 경로를 동일하게 변경한다.

1. PyQGIS 경로는 위 URI를 `QgsRasterLayer(uri, "VWorld Street", "wms")`에 전달하고 유효성을 확인한 뒤 `project.addMapLayer(layer, False)`와 `root.addLayer(layer)`로 프로젝트 루트의 마지막 자식에 추가한다.
2. Headless 경로는 `_layer_id("VWorld Street")`를 트리와 maplayer에 공통으로 사용한다. 루트 `layer-tree-group`의 마지막 직접 자식으로 `providerKey="wms"`, `checked="Qt::Checked"`인 `layer-tree-layer`를 기록한다.
3. Headless `projectlayers`에는 같은 ID와 datasource를 가진 `maplayer type="raster"`를 기록한다. 이 요소는 `layername`, EPSG:3857 `srs`, `provider`(`mapUnitsMm="0"`, `encoding="UTF-8"`, 값 `wms`)를 포함하고 벡터 전용 `renderer-v2`는 포함하지 않는다. XML 직렬화 시 URI의 `&`는 `&amp;`로 인코딩된다.
4. 패널·기본 렌더링 순서의 기준은 루트 레이어 트리의 마지막 자식이라는 위치다. 기존 `layerorder`에도 같은 ID를 마지막 항목으로 기록해 현재 writer 형식과 일관성을 유지하지만, 별도 custom order는 활성화하지 않는다.
5. 기존 `00_기본도` 그룹은 구조 호환성을 위해 유지하지만, 사용자가 승인한 “전체 목록 맨 아래” 요구에 따라 VWorld Street는 그룹 밖에 둔다.

## 제외 범위

- VWorld WFS/Data 카탈로그 및 다운로드 흐름 변경
- API Key 또는 도메인 설정 추가
- TMS for Korea 플러그인 호출·설치 요구
- 기존 프로젝트 소급 변경

## 검증

- Headless 생성 QGZ의 루트 마지막 직접 자식이 표시 상태가 켜진 `VWorld Street`인지 자동 테스트로 확인한다.
- QGS의 해당 `maplayer`가 동일한 ID, raster, `wms`, `EPSG:3857`, 승인된 전체 datasource URI를 저장하고 `layerorder`가 같은 ID를 참조하는지 자동 테스트로 확인한다.
- 프로젝트 생성 통합 테스트와 전체 테스트를 실행한다.
- 설치된 QGIS로 실제 QGZ를 열어 레이어가 유효하고 패널의 마지막 항목이며 노선·AOI 등 다른 레이어가 그 위에 그려지는지 확인한다.

## 수용 기준

- CLI와 QGIS Wizard가 생성한 새 프로젝트 모두 `VWorld Street`를 포함한다.
- 레이어 패널의 전체 목록에서 `VWorld Street`가 맨 아래에 있다.
- `VWorld Street`는 기본 표시 상태가 켜져 있고 다른 모든 레이어의 배경으로 렌더링된다.
- 다른 프로젝트 레이어의 그룹·스타일·초기 범위와 기존 산출물 구조가 유지된다.
