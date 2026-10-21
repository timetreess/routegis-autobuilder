"""Run pyproj work on a Python-owned thread under QGIS's Qt task scheduler.

pyproj 3.7.x can retain an invalid thread-local PROJ context when a QThreadPool
worker thread is reused (pyproj#1509, QGIS#63339).  A Python-managed Thread
has the expected Python thread-state lifecycle.  The QgsTask worker remains
responsible for publishing progress, and does not touch pyproj itself.
"""

from __future__ import annotations

from concurrent.futures import Future
from queue import Empty, Queue
from threading import Thread
from typing import Callable, TypeVar


_T = TypeVar("_T")
Progress = Callable[[int, str], None]


def run_in_python_thread(
    work: Callable[[Progress], _T],
    on_progress: Progress,
) -> _T:
    """Block the supervising QgsTask thread until Python-managed work finishes.

    Only plain Python progress data crosses the boundary.  `on_progress` is
    executed on the calling (QgsTask) thread so Qt/QGIS signals and task progress
    are never touched by the nested Python worker.  The worker is always joined
    before returning or raising, including on cancellation.
    """
    messages: Queue[tuple[int, str]] = Queue()
    outcome: Future[_T] = Future()

    def execute() -> None:
        try:
            outcome.set_result(work(lambda percent, message: messages.put((percent, message))))
        except BaseException as exc:
            outcome.set_exception(exc)

    worker = Thread(target=execute, name="RouteGIS-Python-Worker")
    worker.start()
    try:
        while not outcome.done() or not messages.empty():
            try:
                percent, message = messages.get(timeout=0.05)
            except Empty:
                continue
            on_progress(percent, message)
    finally:
        worker.join()
    return outcome.result()
