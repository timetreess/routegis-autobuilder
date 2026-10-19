import pytest

from routegis.errors import ProviderError
from routegis.providers.download import FetchContext
from routegis.gis import AoiTile
from routegis.models import SpatialLayer


def test_new_planned_work_extends_estimate_only_as_far_as_the_absolute_limit():
    events = []
    context = FetchContext("test", request_budget=2, request_budget_maximum=4,
                           on_progress=events.append)
    context.reserve_requests(3)
    assert context.request_budget == 4
    assert events and "4" in events[0]
    for _ in range(4):
        context.before_request()
    with pytest.raises(ProviderError, match="4"):
        context.before_request()
    assert context.request_count == 4


def test_fixed_budget_context_remains_fixed_without_an_expansion_limit():
    context = FetchContext("test", request_budget=2)
    context.reserve_requests(3)
    assert context.request_budget == 2


def test_routine_progress_is_batched_but_final_counters_are_exact(monkeypatch):
    monkeypatch.setattr("routegis.providers.download.monotonic", lambda: 0.0)
    events = []
    context = FetchContext("test", request_budget=2000, on_progress=events.append)
    tile = AoiTile((0, 0, 1, 1), (0, 0, 1, 1))
    for _ in range(1000):
        context.activate(tile)
        context.before_request()
        context.finish_tile()
    result = context.success_result(SpatialLayer("layer", "Layer", "EPSG:5186", "Polygon", "land"))
    assert len(events) <= 42  # Previously 3,000 routine lines for this workload.
    assert "1000" in events[-1]
    assert result.request_count == result.successful_tile_count == 1000


def test_quiet_progress_uses_time_and_does_not_hide_retries_or_budget_changes(monkeypatch):
    now, events = [0.0], []
    monkeypatch.setattr("routegis.providers.download.monotonic", lambda: now[0])
    context = FetchContext("test", request_budget=100, request_budget_maximum=5000,
                           on_progress=events.append)
    context.before_request()
    now[0] = 5.0
    context.before_request()
    assert len(events) == 2
    context.notify("retry after 2 seconds")
    context.reserve_requests(2)
    assert events[-2] == "retry after 2 seconds"
    assert "102" in events[-1] and "5000" in events[-1]
    result = context.failed_result("layer", "terminal error")
    assert result.request_count == 2 and "2/102" in events[-1]


def test_cancellation_is_checked_even_when_routine_log_is_suppressed(monkeypatch):
    monkeypatch.setattr("routegis.providers.download.monotonic", lambda: 0.0)
    events, cancelled = [], [False]

    def check():
        if cancelled[0]:
            raise RuntimeError("cancelled")

    context = FetchContext("test", request_budget=100, on_progress=events.append, check_cancelled=check)
    context.before_request()
    cancelled[0] = True
    with pytest.raises(RuntimeError, match="cancelled"):
        context.before_request()
    assert len(events) == context.request_count == 1
