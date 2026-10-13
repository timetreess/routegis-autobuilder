from pathlib import Path

import ezdxf
import pytest
from pyproj import Transformer

from routegis.app.project_service import ProjectService
from routegis.errors import CadReadError, CrsError
from routegis.gis.crs_recommendation import BUSINESS_REGIONS, recommend_crs, resolve_business_region


def _bounds(code, longitude, latitude):
    x, y = Transformer.from_crs("EPSG:4326", code, always_xy=True).transform(longitude, latitude)
    return x - 500, y - 500, x + 500, y + 500


@pytest.mark.parametrize("alias", ["전라북도", "전북", "전북특별자치도", " 전라북도 전주시 "])
def test_old_and_new_province_names_resolve_to_the_same_region(alias):
    assert resolve_business_region(alias).name == "전북특별자치도"


def test_all_seventeen_regions_and_aliases_are_accepted():
    assert len(BUSINESS_REGIONS) == 17
    for region in BUSINESS_REGIONS:
        for name in (region.name, *region.aliases):
            assert resolve_business_region(name) == region
    assert resolve_business_region("경기도 광주시").name == "경기도"
    assert resolve_business_region("") is None
    with pytest.raises(CrsError, match="시·도"):
        resolve_business_region("없는지역")


@pytest.mark.parametrize("code,region,longitude,latitude", [
    ("EPSG:5186", "경기도", 127.02, 37.27),
    ("EPSG:5186", "전라북도", 127.15, 35.82),
    ("EPSG:5174", "전북", 127.15, 35.82),
    ("EPSG:5187", "부산", 129.07, 35.18),
    ("EPSG:5179", "경기도", 127.02, 37.27),
    ("EPSG:5178", "경기도", 127.02, 37.27),
    ("EPSG:5182", "제주도", 126.53, 33.50),
    ("EPSG:32652", "경북", 129.36, 36.02),
    ("EPSG:3857", "서울", 126.98, 37.57),
])
def test_real_projected_coordinates_include_the_original_crs(code, region, longitude, latitude):
    candidates = recommend_crs(_bounds(code, longitude, latitude), region)
    original = next(item for item in candidates if item.code == code)
    assert original.longitude == pytest.approx(longitude, abs=1e-4)
    assert original.latitude == pytest.approx(latitude, abs=1e-4)
    assert "범위 내" in original.reason
    assert original.score == candidates[0].score


def test_false_northing_and_wrong_belt_are_screened_using_region():
    candidates = recommend_crs(_bounds("EPSG:5186", 127.15, 35.82), "전라북도")
    codes = [item.code for item in candidates]
    assert codes[0] == "EPSG:5186"
    assert "EPSG:5181" not in codes  # 100 km north with the older false northing.
    assert "EPSG:5187" not in codes  # 2 degrees east in a different belt.


def test_datum_ambiguity_is_preserved_instead_of_claiming_unique_identification():
    candidates = recommend_crs(_bounds("EPSG:5174", 127.15, 35.82), "전라북도")
    codes = {item.code for item in candidates}
    assert {"EPSG:5174", "EPSG:5181", "EPSG:2097"} <= codes
    assert recommend_crs(_bounds("EPSG:5186", 127.15, 35.82))


@pytest.mark.parametrize("bounds", [(127, 37, 127.01, 37.01), (0, 0, 10, 10), (1e12, 1e12, 1e12 + 1, 1e12 + 1)])
def test_nonmatching_or_geographic_coordinates_do_not_get_a_false_recommendation(bounds):
    assert recommend_crs(bounds, "경기도") == []


@pytest.mark.parametrize("bounds", [(float("nan"), 0, 1, 1), (1, 2, 0, 3)])
def test_invalid_extent_is_rejected(bounds):
    with pytest.raises(CrsError, match="좌표 범위"):
        recommend_crs(bounds, "경기도")


def test_analysis_reports_actual_supported_line_coordinates_and_resets_after_failure(tmp_path: Path):
    drawing = tmp_path / "coordinates.dxf"
    document = ezdxf.new()
    document.units = 6
    modelspace = document.modelspace()
    modelspace.add_line((200000, 489000), (201000, 490000))
    modelspace.add_line((1, 2), (1, 2))  # Excluded degenerate entity.
    modelspace.add_text("Not analysis linework", dxfattribs={"insert": (9e6, 9e6)})
    document.saveas(drawing)
    service = ProjectService()
    service.analyze_drawing(drawing)

    report = service.last_coordinate_report
    assert report.bounds == (200000, 489000, 201000, 490000)
    assert report.center == (200500, 489500)
    assert report.insunits == 6
    assert report.unit_name == "Meters"
    assert len(service.last_cad_lines["0"]) == 1
    assert len(service.last_cad_warnings) == 1

    with pytest.raises(CadReadError):
        service.analyze_drawing(tmp_path / "missing.dxf")
    assert service.last_coordinate_report is None
    assert not service.last_cad_lines
