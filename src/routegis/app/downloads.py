from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Queue
from threading import Event

from ..models import DownloadResult, LayerDefinition, SpatialLayer
from ..providers.base import SpatialDataProvider


class _DownloadsStopped(Exception):
    pass


def fetch_provider_groups(
    definitions: list[LayerDefinition],
    aoi: SpatialLayer,
    *,
    create_provider: Callable[[str, Callable[[str], None], Callable[[], None]], SpatialDataProvider],
    on_progress: Callable[[int, str], None],
    on_result: Callable[[int, DownloadResult | Exception], None],
    on_close_error: Callable[[Exception], None],
    provider: SpatialDataProvider | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> None:
    """Run up to three provider groups; deliver all callbacks on the caller thread."""
    if provider is not None:
        # An injected provider may be stateful and belongs to the caller.
        for index, definition in enumerate(definitions):
            if check_cancelled is not None:
                check_cancelled()
            on_progress(index, "수집 중 · 서버 응답/재시도 대기")
            try:
                outcome = provider.fetch_layer(definition, aoi)
            except Exception as exc:
                outcome = exc
            on_result(index, outcome)
        return

    groups: dict[str, list[tuple[int, LayerDefinition]]] = {}
    for index, definition in enumerate(definitions):
        groups.setdefault(definition.provider, []).append((index, definition))
    if not groups:
        return

    events = Queue()
    stopped = Event()

    def fetch_group(items: list[tuple[int, LayerDefinition]]) -> None:
        owned: SpatialDataProvider | None = None
        constructor_error: Exception | None = None
        index = items[0][0]

        def check_stopped() -> None:
            if stopped.is_set():
                raise _DownloadsStopped()

        def progress(message: str) -> None:
            check_stopped()
            events.put(("progress", index, message))

        try:
            for index, definition in items:
                if stopped.is_set():
                    return
                try:
                    progress("수집 중 · 서버 응답/재시도 대기")
                    if constructor_error is not None:
                        raise constructor_error
                    if owned is None:
                        try:
                            owned = create_provider(definition.provider, progress, check_stopped)
                        except Exception as exc:
                            constructor_error = exc
                            raise
                    outcome = owned.fetch_layer(definition, aoi)
                except _DownloadsStopped:
                    return
                except Exception as exc:
                    outcome = exc
                if stopped.is_set():
                    return
                events.put(("result", index, outcome))
        finally:
            close = getattr(owned, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    events.put(("close_error", index, exc))

    executor = ThreadPoolExecutor(max_workers=min(3, len(groups)), thread_name_prefix="routegis-provider")
    try:
        for items in groups.values():
            future = executor.submit(fetch_group, items)
            future.add_done_callback(lambda done: events.put(("done", 0, done)))
        remaining = len(groups)
        while remaining:
            if check_cancelled is not None:
                check_cancelled()
            try:
                kind, index, payload = events.get(timeout=0.25)
            except Empty:
                continue
            if kind == "progress":
                on_progress(index, payload)
            elif kind == "result":
                on_result(index, payload)
            elif kind == "close_error":
                on_close_error(payload)
            else:
                payload.result()
                remaining -= 1
    finally:
        # Callback exceptions (including QgsTask cancellation) are not layer
        # failures. Stop follow-up work, wait for in-flight requests and close
        # clients before the caller removes staging or publishes any files.
        stopped.set()
        executor.shutdown(wait=True, cancel_futures=True)
