import pytest

from routegis.app.project_service import ProjectService


def test_drawing_analysis_reports_stages_and_retains_full_linework(fixture_directory):
    events = []
    service = ProjectService()
    candidates = service.analyze_drawing(fixture_directory / "sample_route.dxf",
                                         on_progress=lambda percent, text: events.append((percent, text)))
    assert candidates and service.last_cad_lines and service.last_coordinate_report
    assert [percent for percent, _ in events] == sorted(percent for percent, _ in events)
    assert events[0][0] == 0 and events[-1][0] == 100
    assert any("객체" in text for _, text in events)


@pytest.mark.parametrize("cancel_at", [20, 85, 100])
def test_drawing_analysis_can_be_cancelled_without_publishing_partial_state(fixture_directory, cancel_at):
    service = ProjectService()

    class Cancelled(Exception):
        pass

    def progress(percent, message):
        if percent >= cancel_at:
            raise Cancelled()

    with pytest.raises(Cancelled):
        service.analyze_drawing(fixture_directory / "sample_route.dxf", on_progress=progress)
    assert service.last_cad_lines == {} and service.last_coordinate_report is None
