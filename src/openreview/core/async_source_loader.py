"""
Asynchronous Source Ingestion Worker.
Probes media metadata, detects image sequences, inspects video containers,
and instantiates SourceItems on background worker threads to ensure zero UI freezing.
"""
from __future__ import annotations

import os
from typing import List, Optional
from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from openreview.models.session_graph import SourceItem


class _SourceLoaderSignals(QObject):
    source_discovered = Signal(object, int, int)  # (SourceItem, current_index, total)
    source_proxy_created = Signal(object, int, int)  # (SourceItem proxy, current_index, total)
    source_resolved = Signal(str, object)  # (proxy_id, SourceItem real)
    batch_completed = Signal(list)  # List[SourceItem]
    progress_message = Signal(str)  # Progress status message


class _SourceProbeTask(QRunnable):
    def __init__(
        self,
        path: str,
        index: int,
        total: int,
        parent_folder_id: Optional[str],
        signals: _SourceLoaderSignals,
        proxy_id: Optional[str] = None,
        is_cancelled_fn=None,
    ):
        super().__init__()
        self.path = path
        self.index = index
        self.total = total
        self.parent_folder_id = parent_folder_id
        self.signals = signals
        self.proxy_id = proxy_id
        self.is_cancelled_fn = is_cancelled_fn

    def run(self) -> None:
        if self.is_cancelled_fn and self.is_cancelled_fn():
            return
        base_name = os.path.basename(self.path)
        self.signals.progress_message.emit(
            f"Probing source {self.index + 1}/{self.total}: {base_name}"
        )
        try:
            from openreview.models.session_graph import build_source_item_from_path
            item = build_source_item_from_path(self.path, self.parent_folder_id)
            if self.is_cancelled_fn and self.is_cancelled_fn():
                return
            if self.proxy_id and item:
                self.signals.source_resolved.emit(self.proxy_id, item)
            self.signals.source_discovered.emit(item, self.index + 1, self.total)
        except Exception:
            self.signals.source_discovered.emit(None, self.index + 1, self.total)


class AsyncSourceLoader(QObject):
    """
    Coordinates non-blocking asynchronous ingestion of media sources.
    Uses worker threads to probe media containers and sequences, dispatching
    discovered SourceItems back to the main GUI thread.
    Modeled on Open RV's progressive source loading and proxy placeholder architecture.
    """

    source_discovered = Signal(object, int, int)  # (SourceItem, current_index, total)
    source_proxy_created = Signal(object, int, int)  # (SourceItem proxy, current_index, total)
    source_resolved = Signal(str, object)  # (proxy_id, SourceItem real)
    before_progressive_loading = Signal(int)  # total count
    after_progressive_loading = Signal(list)  # completed items
    batch_completed = Signal(list)  # List[SourceItem]
    progress_message = Signal(str)

    def __init__(self, parent: Optional[QObject] = None, max_workers: int = 3):
        super().__init__(parent)
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(max(1, min(max_workers, 4)))
        self._signals = _SourceLoaderSignals()
        self._signals.source_discovered.connect(self._on_source_discovered)
        self._signals.source_proxy_created.connect(self.source_proxy_created.emit)
        self._signals.source_resolved.connect(self.source_resolved.emit)
        self._signals.progress_message.connect(self.progress_message.emit)

        self._pending_count = 0
        self._total_count = 0
        self._discovered_items: List[SourceItem] = []
        self._generation = 0

    @property
    def is_loading(self) -> bool:
        return self._pending_count > 0

    def cancel(self) -> None:
        """Cancel current async ingestion tasks."""
        self._generation += 1
        self._pending_count = 0
        self._discovered_items.clear()

    def load_paths_async(
        self,
        paths: List[str],
        parent_folder_id: Optional[str] = None,
        use_progressive_proxies: bool = True,
    ) -> None:
        """
        Queue multiple paths for non-blocking background discovery.
        If use_progressive_proxies is True, immediately creates placeholder proxy items
        (modeled on Open RV's movieProxie) enabling instant zero-latency UI appearance.
        """
        if not paths:
            self.batch_completed.emit([])
            return

        self._generation += 1
        curr_gen = self._generation
        self._total_count = len(paths)
        self._pending_count = len(paths)
        self._discovered_items = []

        self.before_progressive_loading.emit(self._total_count)

        for idx, path in enumerate(paths):
            proxy_id = None
            if use_progressive_proxies:
                from openreview.models.session_graph import create_proxy_source_item
                proxy_item = create_proxy_source_item(path, parent_folder_id)
                proxy_id = proxy_item.id
                self.source_proxy_created.emit(proxy_item, idx + 1, self._total_count)

            task = _SourceProbeTask(
                path=path,
                index=idx,
                total=self._total_count,
                parent_folder_id=parent_folder_id,
                signals=self._signals,
                proxy_id=proxy_id,
                is_cancelled_fn=lambda g=curr_gen: g < self._generation,
            )
            self._thread_pool.start(task)

    def _on_source_discovered(self, item: Optional[SourceItem], index: int, total: int) -> None:
        if item is not None:
            self._discovered_items.append(item)
            self.source_discovered.emit(item, index, total)

        self._pending_count -= 1
        if self._pending_count <= 0:
            completed_batch = list(self._discovered_items)
            self._discovered_items.clear()
            self.progress_message.emit("Source indexing complete.")
            self.after_progressive_loading.emit(completed_batch)
            self.batch_completed.emit(completed_batch)
