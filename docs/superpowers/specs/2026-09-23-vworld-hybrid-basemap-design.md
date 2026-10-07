# VWorld Hybrid 기본 레이어 설계

## 목표

새로 생성하는 모든 QGIS 프로젝트에서 기존 `VWorld Street` 바로 아래에 TMS for Korea의 정식 `VWorld Hybrid` 구성을 추가한다. Hybrid 구성은 전체 루트 목록의 마지막 항목이 된다.

## 레이어 구성

TMS for Korea의 Hybrid는 단일 타일이 아니라 다음 두 XYZ raster 레이어의 합성이다.

1. `VWorld Hybrid Label`
   - URL: `https://xdworld.vworld.kr/2d/Hybrid/service/{z}/{x}/{y}.png`
   - 투명 도로·지명 라벨이며 그룹 안의 위쪽에 둔다.
2. `VWorld Hybrid`
   - URL: `https://xdworld.vworld.kr/2d/Satellite/service/{z}/{x}/{y}.jpeg`
   - 위성영상 배경이며 그룹 안의 아래쪽에 둔다.

두 레이어 모두 QGIS `wms` 공급자의 XYZ raster이며 CRS는 `EPSG:3857`, 확대 수준은 `0`~`18`, `tilePixelRatio`은 `1`이다.

## 레이어 트리 순서

프로젝트 루트의 마지막 두 항목은 다음 순서다.

1. `VWorld Street` 레이어
2. `VWorld Hybrid` 그룹
   1. `VWorld Hybrid Label`
   2. `VWorld Hybrid`

따라서 Hybrid 그룹은 Street보다 아래에 있고 전체 목록의 최하단이다. 그룹과 두 자식은 처음부터 표시 상태를 켠다. Street가 불투명한 상위 기본도이므로 사용자가 Street를 끄면 완성된 Hybrid 지도가 바로 보인다.

## 프로젝트 생성 동작

- Headless QGS XML 경로는 루트의 Street 노드 다음에 checked/expanded 상태의 `VWorld Hybrid` 그룹을 추가하고, 그룹 안에 Label과 위성영상 순서로 `layer-tree-layer`를 기록한다.
- 각 Hybrid 자식은 안정적인 `_layer_id()`를 사용하고 `projectlayers`에 raster `maplayer`로 저장한다.
- 각 datasource URI는 `url=...&zmax=18&zmin=0&type=xyz&tilePixelRatio=1` 형식을 사용한다.
- QGIS 3.34.3 실구동 측정에서 이 트리를 저장하면 `root.layerOrder()`와 QGS `layerorder`가 모두 `Street, Label, 위성영상` 순서로 기록된다. Headless 경로도 이 순서를 그대로 기록한다.
- PyQGIS 경로는 Street를 루트에 추가한 다음 루트 끝에 `VWorld Hybrid` 그룹을 만들고 Label, 위성영상 순서로 `QgsRasterLayer` 두 개를 추가한다. 각 레이어가 유효하지 않으면 `ExportError`를 발생시킨다.
- TMS for Korea 플러그인 설치 여부에는 의존하지 않는다.

## 제외 범위

- 기존 프로젝트 소급 변경
- VWorld WFS/Data 카탈로그 변경
- 기본도 상호 배타적 표시 기능 추가
- VWorld Street의 URL·표시 상태·순서 변경

## 검증

- 생성된 QGS에서 루트 끝 두 자식이 Street 레이어와 Hybrid 그룹인지 자동 검증한다.
- Hybrid 그룹의 자식 순서, 표시 상태, 이름, provider, CRS, datasource URI와 `layerorder` 순서를 자동 검증한다.
- 전체 테스트를 실행한다.
- Headless 및 PyQGIS 생성 QGZ를 QGIS 3.34.3으로 읽어 루트 끝이 `Street, Hybrid 그룹`, 그룹 자식이 `Label, 위성영상` 순서인지 확인한다.
- 두 Hybrid 자식의 공급자가 `wms`, CRS가 `EPSG:3857`, 상태가 valid/visible이며 datasource가 각각 공식 Label·Satellite URL로 정규화되는지 확인한다.

## 수용 기준

- CLI와 QGIS Wizard가 생성한 새 프로젝트 모두 Street 아래에 Hybrid 그룹을 포함한다.
- Hybrid 그룹의 Label은 위성영상 위에 그려져 완성된 Hybrid 지도를 구성한다.
- Street, Hybrid 그룹과 두 자식의 기본 표시 상태가 켜져 있다.
- 기존 그룹·벡터 레이어·스타일·초기 범위와 산출물 구조가 유지된다.
