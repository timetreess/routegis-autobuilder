from collections import defaultdict
from dataclasses import replace
from threading import Barrier, Event, Lock, get_ident

import pytest

from routegis.app.downloads import fetch_provider_groups
from routegis.models import DownloadResult, SpatialLayer
from routegis.providers.catalog import LayerCatalog


def _definitions():
    catalog = LayerCatalog()
    return catalog.select(("power_line", "land_ownership", "ecology_nature",
                           "power_support", "heritage_protection", "river_area"))


def _result(definition):
    return DownloadResult(layer_id=definition.id, status="success")


def test_three_provider_groups_overlap_but_keep_one_client_and_serial_layers_per_group():
    definitions = _definitions()
    barrier, lock = Barrier(3), Lock()
    coordinator = get_ident()
    clients, callback_threads, messages, results = {}, [], [], {}
    active = defaultdict(int)
    calls = defaultdict(list)

    class Client:
        def __init__(self, name, progress):
            self.name, self.progress = name, progress
            self.thread = get_ident()
            self.closed = False

        def fetch_layer(self, definition, aoi):
            assert get_ident() == self.thread != coordinator
            with lock:
                active[self.name] += 1
                assert active[self.name] == 1
                calls[self.name].append(definition.id)
            if len(calls[self.name]) == 1:
                barrier.wait(timeout=3)
            self.progress(f"details:{definition.id}")
            with lock:
                active[self.name] -= 1
            return _result(definition)

        def close(self):
            assert get_ident() == self.thread
            self.closed = True

    def create(name, progress, check_cancelled):
        assert name not in clients
        clients[name] = Client(name, progress)
        return clients[name]

    def progress(index, message):
        callback_threads.append(get_ident())
        messages.append((index, message))

    def accept(index, outcome):
        callback_threads.append(get_ident())
        results[index] = outcome

    fetch_provider_groups(definitions, SpatialLayer("aoi", "AOI", "EPSG:5186", "Polygon", "route"),
                          create_provider=create, on_progress=progress, on_result=accept,
                          on_close_error=lambda error: pytest.fail(str(error)))
    assert set(clients) == {"osm", "vworld", "ecology"}
    assert all(client.closed for client in clients.values())
    assert set(callback_threads) == {coordinator}
    for name in clients:
        assert calls[name] == [d.id for d in definitions if d.provider == name]
    assert [results[i].layer_id for i in range(len(definitions))] == [d.id for d in definitions]
    assert all(definitions[i].id in message for i, message in messages if message.startswith("details:"))


def test_executor_never_runs_more_than_three_provider_groups():
    definitions = [replace(_definitions()[0], id=f"layer-{i}", provider=f"provider-{i}") for i in range(6)]
    barrier, lock = Barrier(3), Lock()
    active = maximum = 0
    results, closed = [], []

    class Client:
        def fetch_layer(self, definition, aoi):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            barrier.wait(timeout=3)
            with lock:
                active -= 1
            return _result(definition)

        def close(self):
            closed.append(True)

    fetch_provider_groups(definitions, None, create_provider=lambda *_: Client(),
                          on_progress=lambda *_: None, on_result=lambda _, result: results.append(result),
                          on_close_error=lambda error: pytest.fail(str(error)))
    assert maximum == 3
    assert len(results) == len(closed) == 6
    assert all(isinstance(result, DownloadResult) for result in results)


def test_constructor_fetch_and_close_failures_are_isolated_by_provider():
    attempts, outcomes, close_errors = [], {}, []

    class Client:
        def fetch_layer(self, definition, aoi):
            if definition.id == "power_line":
                raise ValueError("one layer failed")
            return _result(definition)

        def close(self):
            raise ValueError("close failed")

    def create(name, progress, check_cancelled):
        attempts.append(name)
        if name == "vworld":
            raise ValueError("constructor failed")
        return Client()

    definitions = _definitions()
    fetch_provider_groups(definitions, None, create_provider=create, on_progress=lambda *_: None,
                          on_result=lambda index, result: outcomes.__setitem__(definitions[index].id, result),
                          on_close_error=close_errors.append)
    assert attempts.count("vworld") == 1
    assert str(outcomes["land_ownership"]) == str(outcomes["heritage_protection"]) == "constructor failed"
    assert str(outcomes["power_line"]) == "one layer failed"
    assert isinstance(outcomes["power_support"], DownloadResult)
    assert len(close_errors) == 2


def test_injected_provider_stays_sequential_on_caller_thread_and_is_not_closed():
    coordinator, calls = get_ident(), []

    class Client:
        def fetch_layer(self, definition, aoi):
            assert get_ident() == coordinator
            calls.append(definition.id)
            return _result(definition)

        def close(self):
            pytest.fail("injected provider belongs to caller")

    definitions = _definitions()
    fetch_provider_groups(definitions, None, provider=Client(),
                          create_provider=lambda *_: pytest.fail("unexpected factory"),
                          on_progress=lambda *_: None, on_result=lambda *_: None,
                          on_close_error=lambda error: pytest.fail(str(error)))
    assert calls == [d.id for d in definitions]


def test_callback_cancellation_stops_followup_work_and_closes_all_workers():
    barrier = Barrier(3)
    closed, calls, callbacks = [], [], []

    class Cancelled(Exception):
        pass

    class Client:
        def __init__(self, progress, check_cancelled):
            self.progress, self.check_cancelled = progress, check_cancelled

        def fetch_layer(self, definition, aoi):
            calls.append(definition.id)
            barrier.wait(timeout=3)
            self.progress("cancel boundary")
            # Simulate an in-flight request; cancellation is noticed at its next boundary.
            Event().wait(timeout=0.2)
            self.check_cancelled()
            pytest.fail("request continued after cancellation")
            return _result(definition)

        def close(self):
            closed.append(True)

    def progress(index, message):
        if message == "cancel boundary":
            raise Cancelled("user cancelled")

    with pytest.raises(Cancelled, match="user cancelled"):
        fetch_provider_groups(_definitions(), None, create_provider=lambda _, progress, check: Client(progress, check),
                              on_progress=progress, on_result=lambda *args: callbacks.append(args),
                              on_close_error=lambda error: pytest.fail(str(error)))
    assert len(calls) == len(closed) == 3
    assert not callbacks


def test_quiet_download_can_be_cancelled_without_emitting_another_progress_message():
    requested, release = Event(), Event()
    barrier, closed = Barrier(3, action=requested.set), []

    class Cancelled(Exception):
        pass

    class Client:
        def fetch_layer(self, definition, aoi):
            barrier.wait(timeout=3)
            assert release.wait(timeout=3)
            return _result(definition)

        def close(self):
            closed.append(True)

    def check():
        if requested.is_set():
            release.set()
            raise Cancelled()

    with pytest.raises(Cancelled):
        fetch_provider_groups(_definitions()[:3], None, create_provider=lambda *_: Client(),
                              on_progress=lambda *_: None, on_result=lambda *_: pytest.fail("cancelled result published"),
                              on_close_error=lambda error: pytest.fail(str(error)), check_cancelled=check)
    assert len(closed) == 3
