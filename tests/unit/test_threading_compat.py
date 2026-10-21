"""Regression coverage for Python-owned work under Qt/QGIS task scheduling."""

from __future__ import annotations

from threading import Event, get_ident
from time import sleep

import pytest

from routegis.threading_compat import run_in_python_thread


def test_worker_runs_off_supervisor_and_progress_is_published_on_supervisor():
    supervisor = get_ident()
    worker_ids = []
    messages = []

    def work(report):
        worker_ids.append(get_ident())
        report(0, "start")
        report(35, "CRS validated")
        report(99, "done")
        return "completed"

    result = run_in_python_thread(
        work,
        lambda percent, message: messages.append((get_ident(), percent, message)),
    )

    assert result == "completed"
    assert worker_ids and worker_ids[0] != supervisor
    assert messages == [
        (supervisor, 0, "start"),
        (supervisor, 35, "CRS validated"),
        (supervisor, 99, "done"),
    ]


def test_worker_exception_is_raised_after_progress_is_delivered():
    updates = []

    def work(report):
        report(5, "before error")
        raise ValueError("bad CRS")

    with pytest.raises(ValueError, match="bad CRS"):
        run_in_python_thread(work, lambda percent, message: updates.append((percent, message)))
    assert updates == [(5, "before error")]


def test_worker_is_joined_when_progress_publisher_fails():
    finished = Event()

    def work(report):
        try:
            report(10, "start")
            sleep(0.02)
        finally:
            finished.set()

    with pytest.raises(RuntimeError, match="GUI failed"):
        run_in_python_thread(work, lambda *_: (_ for _ in ()).throw(RuntimeError("GUI failed")))
    assert finished.is_set()


def test_repeated_crs_operations_use_fresh_python_owned_threads():
    from pyproj import CRS, Transformer

    supervisor = get_ident()

    def work(report):
        assert get_ident() != supervisor
        crs = CRS.from_user_input("EPSG:5186")
        assert crs.is_projected
        x, y = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(127.0, 37.5)
        report(50, "transformed")
        return x, y

    for _ in range(12):
        progress = []
        x, y = run_in_python_thread(work, lambda p, m: progress.append((p, m)))
        assert 100000 < x < 300000
        assert 300000 < y < 700000
        assert progress == [(50, "transformed")]
