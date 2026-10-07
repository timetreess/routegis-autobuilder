from pathlib import Path

import pytest
from shapely.geometry import LineString

from routegis.app.project_service import ProjectService
from routegis.cad.reader import DxfReader
from routegis.cad.route_detector import assemble_route, rank_candidates
from routegis.errors import CadReadError, RouteAssemblyError


def test_golden_dxf_inventory_and_route(fixture_directory: Path):
    reader = DxfReader(fixture_directory / "sample_route.dxf", chord_tolerance=0.05)
    candidates = rank_candidates(reader.inventory(snap_tolerance=0.1))

    assert candidates[0].name == "CENTER"
    assert candidates[0].entity_count == 3
    assert set(candidates[0].entity_types) == {"ARC", "LINE"}

    route = assemble_route(reader.read_layer("CENTER"), tolerance=0.1)
    assert route.geom_type == "LineString"
    assert route.length == pytest.approx(2000 + 3.141592653589793 * 250, rel=5e-4)
    assert route.bounds == pytest.approx((200000.0, 489012.955691, 202500.0, 489512.955691))


def test_lwpolyline_is_extracted(tmp_path: Path):
    import ezdxf

    path = tmp_path / "lwpolyline.dxf"
    document = ezdxf.new()
    document.modelspace().add_lwpolyline([(0, 0), (10, 0), (20, 10)], dxfattribs={"layer": "CENTER"})
    document.saveas(path)

    lines = DxfReader(path).read_layer("CENTER")
    assert len(lines) == 1
    assert lines[0].coords[-1] == pytest.approx((20, 10))


def test_zero_length_entities_are_skipped_and_reported(tmp_path: Path):
    import ezdxf

    path = tmp_path / "zero_length.dxf"
    document = ezdxf.new()
    modelspace = document.modelspace()
    modelspace.add_line((0, 0), (10, 0), dxfattribs={"layer": "CENTER"})
    zero_line = modelspace.add_line((5, 5), (5, 5), dxfattribs={"layer": "AUX"})
    modelspace.add_lwpolyline([(0, 1), (10, 1)], dxfattribs={"layer": "AUX"})
    zero_polyline = modelspace.add_lwpolyline([(7, 7), (7, 7)], dxfattribs={"layer": "AUX"})
    document.saveas(path)

    reader = DxfReader(path)
    inventory = {item.name: item for item in reader.inventory()}
    lines_by_layer = reader.lines_by_layer()

    assert set(inventory) == {"AUX", "CENTER"}
    assert inventory["CENTER"].entity_count == 1
    assert inventory["AUX"].entity_count == 1
    assert inventory["AUX"].entity_types == ("LWPOLYLINE",)
    assert len(lines_by_layer["CENTER"]) == 1
    assert len(lines_by_layer["AUX"]) == 1
    assert {(item.entity_type, item.layer, item.handle) for item in reader.skipped_entities} == {
        ("LINE", "AUX", zero_line.dxf.handle),
        ("LWPOLYLINE", "AUX", zero_polyline.dxf.handle),
    }
    assert {item.reason for item in reader.skipped_entities} == {"zero_length"}


def test_all_zero_length_drawing_has_clear_analysis_error(tmp_path: Path):
    import ezdxf

    path = tmp_path / "only_zero_length.dxf"
    document = ezdxf.new()
    modelspace = document.modelspace()
    modelspace.add_line((5, 5), (5, 5), dxfattribs={"layer": "AUX"})
    modelspace.add_lwpolyline([(7, 7), (7, 7)], dxfattribs={"layer": "AUX"})
    document.saveas(path)

    with pytest.raises(CadReadError, match="모두 길이 0"):
        ProjectService().analyze_drawing(path)


def test_route_assembly_rejects_disconnected_segments():
    lines = [LineString([(0, 0), (1, 0)]), LineString([(3, 0), (4, 0)])]

    with pytest.raises(RouteAssemblyError, match="연결되지 않은"):
        assemble_route(lines, tolerance=0.1)


def test_route_assembly_rejects_branches():
    lines = [
        LineString([(0, 0), (1, 0)]),
        LineString([(1, 0), (2, 0)]),
        LineString([(1, 0), (1, 1)]),
    ]

    with pytest.raises(RouteAssemblyError, match="분기점"):
        assemble_route(lines, tolerance=0.01)
