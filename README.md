# RouteGIS AutoBuilder

<p align="center">
  <img src="plugin/icon.png" alt="RouteGIS AutoBuilder 아이콘" width="96">
</p>

> DXF 도면 한 장을 사업분석용 QGIS 프로젝트로 정리해 주는 도구.

RouteGIS AutoBuilder는 토목 도면의 선 객체를 읽고, 사업구간 주변 범위를 만든 뒤, 필요한 공간정보와 함께 QGIS 프로젝트로 정리하는 도구입니다.

노선 검토를 시작할 때는 CAD 도면을 GIS 데이터로 바꾸고, 좌표계를 맞추고, Buffer를 만들고, 여러 기관의 공간정보를 내려받아 같은 구조로 정리해야 합니다. 이 과정은 어렵다기보다 반복적이고, 프로젝트마다 결과 폴더와 레이어 구성이 달라지기 쉽습니다.

이 프로젝트는 그 반복 작업을 한 번에 처리해, 사용자가 데이터 변환보다 실제 검토에 더 빨리 들어갈 수 있게 만드는 것을 목표로 합니다.

---

## 일단 실행하면 무엇이 달라지나요?

사용자는 사업명, DXF 도면, 좌표계와 분석 거리를 지정하고 필요한 공간정보를 고릅니다. 그러면 RouteGIS AutoBuilder가 뒤에서 다음 작업을 수행합니다.

1. DXF의 유효한 `LINE`, `POLYLINE`, `LWPOLYLINE`, `ARC` 객체를 읽습니다.
2. 모든 선 객체를 개별 Feature로 보존하면서 하나의 사업 선형 레이어로 정리합니다.
3. 지정한 거리만큼 사업구간 주변 Buffer를 만들어 분석 범위를 정합니다.
4. 선택한 VWorld, 국립생태원, OpenStreetMap 공간정보를 분석 범위에 맞춰 수집합니다.
5. 좌표계와 Geometry를 확인하고 GeoPackage와 Shapefile로 저장합니다.
6. 그룹, 스타일, 출처와 라이선스가 정리된 QGIS 프로젝트를 만듭니다.
7. 어떤 데이터가 성공하거나 실패했는지 Manifest와 처리 보고서에 기록합니다.

결과적으로 사용자는 개별 파일을 하나씩 불러오고 스타일을 다시 지정하는 대신, 생성된 `.qgz`를 열어 바로 사업구간과 주변 공간정보를 검토할 수 있습니다.

---

## 실무에서 특히 쓸모 있는 순간

### 1. 노선도면을 처음 받아 주변 현황을 빠르게 봐야 할 때

설계 초기나 입찰 검토 단계에서는 도면만 있고 GIS 자료가 정리되지 않은 경우가 많습니다. DXF와 좌표계만 확인하면 생태, 토지, 국가유산, 하천, 전력시설 같은 기본 레이어를 같은 프로젝트 안에서 빠르게 비교할 수 있습니다.

### 2. CAD 레이어가 많아 어떤 선을 남길지 결정하기 어려울 때

QGIS Wizard는 특정 중심선 레이어를 고르도록 요구하지 않습니다. 도면에서 읽을 수 있는 모든 유효 선 객체를 분석 범위의 기준으로 사용하고, 원본 CAD 레이어명은 `dxf_layer` 속성에 남깁니다. 무엇이 포함되었는지 나중에 다시 추적할 수 있습니다.

### 3. 긴 노선 때문에 공간정보 요청이 자주 끊길 때

장거리 사업구간은 한 번의 API 요청으로 처리하기 어렵습니다. RouteGIS AutoBuilder는 분석 범위를 공급자별 격자로 나누고, 응답 제한에 걸린 구간은 더 작은 타일로 나눠 요청합니다. 일부 공급자 데이터가 실패하더라도 성공한 레이어와 프로젝트 결과는 보존합니다.

### 4. 다른 팀원에게 검토 자료를 넘겨야 할 때

GeoPackage만 만드는 데서 끝나지 않고, 실제 Shapefile, QGIS 그룹과 스타일, 데이터 출처, 라이선스, 처리 보고서를 함께 남깁니다. 누가 프로젝트를 다시 열어도 어떤 데이터가 어디서 왔고 무엇이 실패했는지 확인할 수 있습니다.

---

## 결과물은 어떻게 구성되나요?

| 결과 | 역할 |
|---|---|
| `<사업명>.qgz` | 그룹, 스타일, 초기 화면 범위가 설정된 QGIS 프로젝트 |
| `data/project.gpkg` | UTF-8 기반의 전체 분석 데이터 저장소 |
| `shp/` | 분야별 Shapefile과 필드명 대응표 |
| `source/original.dxf` | 실행에 사용한 원본 도면 사본 |
| `reports/` | 사람이 읽는 HTML 보고서와 JSON 처리 결과 |
| `project_manifest.json` | 입력값, 좌표계, 레이어, 공급원과 실행 상태 |
| `logs/app.log` | API Key가 제거된 실행 로그 |

Shapefile 속성은 국내 CAD/GIS 환경과의 호환을 위해 EUC-KR로 저장합니다. EUC-KR로 표현할 수 없는 문자가 있으면 해당 레이어는 문자를 손실시키지 않도록 GeoPackage를 참조합니다.

---

## QGIS 플러그인으로 설치하기

가장 일반적인 사용 방법입니다. **QGIS 3.34 이상**, 내장 Python **3.9 이상**인 Windows 환경을 지원합니다.

1. [GitHub Releases](https://github.com/timetreess/routegis-autobuilder/releases)에서 `routegis_autobuilder.<version>.zip`을 내려받습니다.
2. ZIP을 압축 해제하지 않습니다.
3. QGIS에서 **플러그인 → 플러그인 관리 및 설치 → ZIP 파일에서 설치**를 엽니다.
4. 내려받은 ZIP을 선택하고 **플러그인 설치**를 누릅니다.
5. **벡터 → RouteGIS AutoBuilder** 또는 툴바 아이콘으로 실행합니다.

업데이트할 때도 새 Release ZIP을 같은 방법으로 설치하면 됩니다.

---

## CLI 설치는 AI 에이전트에게 맡기세요

CLI를 사용하려고 설치 명령어와 Python 환경 구성을 직접 맞출 필요는 없습니다. 아래 문장을 Claude Code, Codex, opencode 같은 코딩 에이전트에게 그대로 전달하세요.

> 이 레포를 내 Windows 환경에 CLI로 설치해줘: https://github.com/timetreess/routegis-autobuilder
>
> Python 3.9 이상을 사용하고, 실제 API Key나 사내 DXF가 GitHub와 커밋에 들어가지 않게 설정해줘.
>
> 설치가 끝나면 fixture 데이터로 프로젝트를 한 번 생성해서 정상 동작도 확인해줘.

에이전트가 알아야 할 기본 정보는 다음과 같습니다.

| 항목 | 값 |
|---|---|
| Python | 3.9 이상 |
| CLI 명령 | `routegis` |
| VWorld Key 환경변수 | `VWORLD_API_KEY` |
| 공공데이터포털 서비스키 환경변수 | `DATA_GO_KR_SERVICE_KEY` |
| 입력 도면 | DXF |
| 기본 출력 | QGZ, GeoPackage, Shapefile, 보고서 |

설치 이후에도 “이 DXF를 EPSG:5186으로 분석해서 1 km 범위의 QGIS 프로젝트를 만들어줘”처럼 에이전트에게 작업을 요청하면 됩니다. 에이전트는 `routegis analyze`와 `routegis build`의 옵션을 현재 도면과 목적에 맞게 구성할 수 있습니다.

---

## API Key는 어떻게 처리하나요?

공간정보 공급자에 따라 필요한 Key가 다릅니다.

| 공급자 | 필요한 값 |
|---|---|
| VWorld | VWorld API Key |
| 국립생태원 | 공공데이터포털 서비스키 |
| OpenStreetMap Overpass | 없음 |

VWorld 공식 WFS 요청은 API Key만으로 사용할 수 있으며, 데스크톱 QGIS Wizard에는 등록 도메인 입력란이 없습니다.

Wizard에서 입력한 Key는 다음 실행 때 다시 사용할 수 있도록 현재 사용자의 QGIS 설정에 저장됩니다. 환경변수와 설치 플러그인의 `.env` 값이 저장된 입력보다 우선합니다. 입력란을 비운 뒤 다른 곳을 클릭하거나 Wizard를 닫으면 저장된 값도 삭제됩니다.

입력란은 Password 형식으로 가려지지만 QGIS 설정은 암호화된 Credential Store가 아닙니다. 공용 PC에서는 Key를 저장하지 마세요. Key는 생성된 QGIS 프로젝트, Manifest, 보고서와 로그에는 기록하지 않습니다.

---

## 기본으로 다루는 공간정보

기본 프로필은 다음 7개 레이어를 사용합니다.

| 공간정보 | 공급원 |
|---|---|
| 생태자연도 | 국립생태원 |
| 토지소유공간정보 | VWorld |
| 국가유산 지정·보호구역 | VWorld |
| 송전선로 | OpenStreetMap |
| 송전철탑·지지물 | OpenStreetMap |
| 하천 중심선 | OpenStreetMap |
| 하천구역·수면 | OpenStreetMap |

VWorld Key가 있으면 Wizard에서 실시간 WFS 목록을 불러와 추가 레이어를 선택할 수 있습니다. 각 레이어의 공급원, 원본 데이터 ID, 발행기관과 라이선스는 결과 프로젝트와 보고서에 함께 기록됩니다.

---

## 꼭 알아야 할 한계

- DWG는 직접 읽지 않습니다. CAD 프로그램에서 DXF로 저장한 뒤 사용해야 합니다.
- 좌표계는 시스템이 임의로 확정하지 않습니다. 도면에 사용된 미터 단위 투영좌표계를 사용자가 확인해야 합니다.
- 문자, 치수, 블록 등 지원하지 않는 DXF 객체는 분석 범위에 포함하지 않습니다.
- 시작점과 끝점이 같거나 중복 좌표만 있는 길이 0의 선 객체는 제외하고 보고서에 경고를 남깁니다.
- 외부 공간정보의 정확성, 최신성, 이용 가능 여부와 라이선스는 각 공급기관의 정책을 따릅니다.
- API 호출이 성공했다는 사실이 해당 데이터의 설계·인허가 적합성을 보증하지는 않습니다.
- 이 도구는 반복 작업을 줄이지만 좌표계 판단, 결과 검토와 최종 기술적 책임을 대신하지 않습니다.

---

## 기여와 보안

- 기여: [CONTRIBUTING.md](CONTRIBUTING.md)
- 보안: [SECURITY.md](SECURITY.md)
- 변경 기록: [CHANGELOG.md](CHANGELOG.md)
- 라이선스: [MIT](LICENSE)
