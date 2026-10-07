# Korean QGIS Layer Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 생성되는 QGIS 프로젝트의 7개 레이어 그룹명을 번호 접두어를 유지한 한글명으로 변경한다.

**Architecture:** `src/routegis/qgis/project.py`의 단일 `GROUPS` 튜플을 한글명으로 바꾸고, 카테고리 및 원본 CAD 그룹 조회가 같은 값을 반환하도록 맞춘다. Headless QGS와 PyQGIS 네이티브 경로가 이 공통 값을 사용하므로 별도 구조 변경은 하지 않는다.

**Tech Stack:** Python 3.9+, XML ElementTree, PyQGIS 호환 프로젝트 작성기, pytest

---

### Task 1: 레이어 그룹명 한글화

**Files:**
- Modify: `src/routegis/qgis/project.py:15`
- Modify: `src/routegis/qgis/project.py:142`
- Modify: `src/routegis/qgis/project.py:240`
- Modify: `src/routegis/qgis/project.py:241`
- Modify: `tests/integration/test_pipeline.py:67`
- Verify: 생성된 임시 `.qgz` 내부 `.qgs`

- [ ] **Step 1: 공통 그룹명과 카테고리 매핑 변경**

`GROUPS`와 `_group_for()`의 반환값을 설계 문서의 다음 이름으로 교체하고, Headless 및 PyQGIS 경로에서 원본 CAD를 추가할 때 사용하는 `"99_SOURCE"` 키도 `"99_원본자료"`로 바꾼다.

```python
GROUPS = (
    "00_기본도",
    "01_노선",
    "02_지적",
    "03_토지이용",
    "04_행정구역",
    "05_시설물",
    "99_원본자료",
)
```

- [ ] **Step 2: 통합 테스트 기대값 변경**

`tests/integration/test_pipeline.py`의 QGS 그룹명 검증을 다음 한글 그룹명으로 바꾼다.

```python
assert {"01_노선", "02_지적", "99_원본자료"} <= groups
```

- [ ] **Step 3: 정적 진단 실행**

`src/routegis/qgis/project.py`와 `tests/integration/test_pipeline.py`에 대해 LSP 진단을 실행한다.

Expected: 오류와 경고가 없다.

- [ ] **Step 4: 프로젝트 작성기 직접 실행**

임시 경로와 최소 `SpatialLayer` 목록으로 `write_qgis_project()`를 호출해 `.qgz`를 생성한다.

Expected: 명령이 성공하고 QGZ 파일이 생성된다.

- [ ] **Step 5: 사용자 표면 결과 확인**

QGZ 내부 QGS의 최상위 `layer-tree-group` 이름을 파싱한다.

Expected: `00_기본도`, `01_노선`, `02_지적`, `03_토지이용`, `04_행정구역`, `05_시설물`, `99_원본자료`가 이 순서로 존재하며 기존 영문 이름은 없다.

- [ ] **Step 6: 관련 테스트 실행**

Run: `& ".venv\Scripts\python.exe" -m pytest`

Expected: 전체 테스트가 통과한다.
