import struct
from pathlib import Path

import ezdxf
import pytest

from routegis.app.project_service import ProjectService
from routegis.errors import CrsError


def test_preview_export_writes_one_shapefile_per_populated_cad_layer(tmp_path: Path):
    drawing = tmp_path / "preview.dxf"
    document = ezdxf.new()
    modelspace = document.modelspace()
    modelspace.add_line((200000, 489000), (201000, 489000), dxfattribs={"layer": "CENTER"})
    modelspace.add_line((201000, 489000), (202000, 489000), dxfattribs={"layer": "CENTER"})
    modelspace.add_line((200000, 488500), (202000, 488500), dxfattribs={"layer": "ROAD_EDGE"})
    document.saveas(drawing)

    previews = ProjectService().create_preview_shapefiles(
        drawing,
        source_crs="EPSG:5186",
        output_directory=tmp_path / "preview",
    )

    assert set(previews) == {"CENTER", "ROAD_EDGE"}
    assert struct.unpack("<I", previews["CENTER"].with_suffix(".dbf").read_bytes()[4:8]) == (2,)
    assert struct.unpack("<I", previews["ROAD_EDGE"].with_suffix(".dbf").read_bytes()[4:8]) == (1,)
    for path in previews.values():
        assert path.is_file()
        assert path.with_suffix(".shx").is_file()
        assert path.with_suffix(".prj").is_file()
        assert "Korea" in path.with_suffix(".prj").read_text(encoding="utf-8")


def test_preview_failure_does_not_create_output_directory(tmp_path: Path):
    drawing = tmp_path / "preview.dxf"
    document = ezdxf.new()
    document.modelspace().add_line((127, 37), (127.01, 37), dxfattribs={"layer": "CENTER"})
    document.saveas(drawing)
    output_directory = tmp_path / "preview"

    with pytest.raises(CrsError, match="투영좌표계"):
        ProjectService().create_preview_shapefiles(
            drawing,
            source_crs="EPSG:4326",
            output_directory=output_directory,
        )

    assert not output_directory.exists()
