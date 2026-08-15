"""Drive a library scan: walk, read metadata, and use the cache where it can.

Kept apart from the UI so the scanning logic can be exercised without a Tk
window, and so app.py stays about widgets.
"""

import logging
import os
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from wavfinder.index_cache import IndexCache
from wavfinder.metadata import extract_metadata
from wavfinder.models import WavMetadata
from wavfinder.scanner import scan_wav_files

logger = logging.getLogger(__name__)

# How many files to accumulate before handing a batch to the caller. Small
# enough that results appear promptly, large enough that a huge library does
# not drown the UI in updates.
BATCH_SIZE = 100


@dataclass
class ScanStats:
    indexed: int = 0
    from_cache: int = 0
    unreadable: int = 0
    pruned: int = 0
    cancelled: bool = False


def scan_library(
    roots: Sequence[Path],
    on_batch: Callable[[list[WavMetadata]], None],
    *,
    cache: IndexCache | None = None,
    cancel: threading.Event | None = None,
    on_progress: Callable[[ScanStats], None] | None = None,
    batch_size: int = BATCH_SIZE,
) -> ScanStats:
    """Scan *roots*, calling *on_batch* with each batch of metadata found."""
    stats = ScanStats()
    batch: list[WavMetadata] = []
    pending_cache: list[tuple[WavMetadata, int, int]] = []
    seen: set[str] = set()

    def flush() -> None:
        if batch:
            on_batch(list(batch))
            batch.clear()
        if cache is not None and pending_cache:
            cache.put_many(pending_cache)
            pending_cache.clear()

    for path in scan_wav_files(roots):
        if cancel is not None and cancel.is_set():
            stats.cancelled = True
            break

        try:
            st = os.stat(path)
        except OSError:
            stats.unreadable += 1
            continue

        resolved = path.resolve()
        seen.add(str(resolved))

        meta = None
        if cache is not None:
            meta = cache.get(resolved, st.st_mtime_ns, st.st_size)
            if meta is not None:
                stats.from_cache += 1

        if meta is None:
            meta = extract_metadata(path)
            if meta is None:
                stats.unreadable += 1
                continue
            if cache is not None:
                pending_cache.append((meta, st.st_mtime_ns, st.st_size))

        batch.append(meta)
        stats.indexed += 1

        if len(batch) >= batch_size:
            flush()
            if on_progress is not None:
                on_progress(stats)

    flush()

    # A cancelled scan has not seen the whole library, so pruning would throw
    # away perfectly good cache rows for files it simply never reached.
    if cache is not None and not stats.cancelled:
        stats.pruned = cache.prune(seen, [str(Path(r).resolve()) for r in roots])

    if on_progress is not None:
        on_progress(stats)
    return stats


def reindex_file(path: Path, cache: IndexCache | None = None) -> WavMetadata | None:
    """Re-read one file, refreshing its cache row. Used after a move or copy."""
    meta = extract_metadata(path)
    if meta is None:
        return None
    if cache is not None:
        try:
            st = os.stat(path)
        except OSError:
            return meta
        cache.put(meta, st.st_mtime_ns, st.st_size)
    return meta


def iter_roots(paths: Iterable[Path]) -> list[Path]:
    """Keep only the roots that currently exist, so a missing drive is skipped."""
    return [p for p in paths if p.is_dir()]
