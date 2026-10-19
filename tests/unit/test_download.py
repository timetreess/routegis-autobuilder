import pytest

from routegis.errors import ProviderError
from routegis.providers.download import FetchContext


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
