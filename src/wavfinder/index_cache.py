"""Persist the extracted metadata so relaunching does not re-read the library.

Parsing a 1-2 TB sound library takes a long time, and almost nothing in it
changes between sessions. We keep the parsed metadata in a small SQLite file
keyed by absolute path, and treat a row as valid while the file's size and
modification time still match what we recorded.
"""

import json
import logging
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from wavfinder.config import config_dir
from wavfinder.models import WavMetadata

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path      TEXT PRIMARY KEY,
    mtime_ns  INTEGER NOT NULL,
    size      INTEGER NOT NULL,
    metadata  TEXT NOT NULL
);
"""


def cache_path() -> Path:
    return config_dir() / "index.sqlite3"


class IndexCache:
    """A path -> metadata store guarded by (mtime, size).

    Every method degrades to a no-op if the database cannot be opened: a broken
    cache should cost the user a rescan, never the ability to run the app.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or cache_path()
        self._conn: sqlite3.Connection | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # The scan runs on a worker thread and the UI reads on the main one.
            self._conn = sqlite3.connect(
                self.path, check_same_thread=False, isolation_level=None
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
        except (OSError, sqlite3.Error):
            logger.warning("Index cache unavailable at %s", self.path, exc_info=True)
            self._conn = None

    @property
    def available(self) -> bool:
        return self._conn is not None

    # ----------------------------------------------------------- reading --
    def get(self, path: Path, mtime_ns: int, size: int) -> WavMetadata | None:
        """Return cached metadata if the file is unchanged, else None."""
        if self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT mtime_ns, size, metadata FROM files WHERE path = ?",
                (str(path),),
            ).fetchone()
        except sqlite3.Error:
            return None
        if row is None or row[0] != mtime_ns or row[1] != size:
            return None
        try:
            return _from_json(row[2])
        except (ValueError, KeyError, TypeError):
            logger.debug("Discarding corrupt cache row for %s", path)
            return None

    # ----------------------------------------------------------- writing --
    def put(self, meta: WavMetadata, mtime_ns: int, size: int) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO files (path, mtime_ns, size, metadata)"
                " VALUES (?, ?, ?, ?)",
                (str(meta.file_path), mtime_ns, size, _to_json(meta)),
            )
        except sqlite3.Error:
            logger.debug("Could not cache %s", meta.file_path, exc_info=True)

    def put_many(self, rows: Iterable[tuple[WavMetadata, int, int]]) -> None:
        """Insert a batch inside one transaction -- far faster row by row."""
        if self._conn is None:
            return
        payload = [
            (str(meta.file_path), mtime_ns, size, _to_json(meta))
            for meta, mtime_ns, size in rows
        ]
        if not payload:
            return
        try:
            with self._conn:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO files (path, mtime_ns, size, metadata)"
                    " VALUES (?, ?, ?, ?)",
                    payload,
                )
        except sqlite3.Error:
            logger.debug("Could not cache a batch of %d files", len(payload), exc_info=True)

    def forget(self, path: Path) -> None:
        """Drop a row, e.g. after the file is moved out of the library."""
        if self._conn is None:
            return
        try:
            self._conn.execute("DELETE FROM files WHERE path = ?", (str(path),))
        except sqlite3.Error:
            pass

    def prune(self, keep: set[str], roots: Iterable[str]) -> int:
        """Forget files a scan no longer finds. Returns how many were dropped.

        Only rows underneath *roots* are considered, so pruning after scanning
        one library cannot throw away the cache for a library the user happens
        to have switched off.
        """
        if self._conn is None:
            return 0
        prefixes = tuple(str(root).rstrip("/\\") for root in roots)
        if not prefixes:
            return 0
        try:
            with self._conn:
                stale = [
                    row[0]
                    for row in self._conn.execute("SELECT path FROM files")
                    if row[0].startswith(prefixes) and row[0] not in keep
                ]
                self._conn.executemany(
                    "DELETE FROM files WHERE path = ?", [(p,) for p in stale]
                )
            return len(stale)
        except sqlite3.Error:
            return 0

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None


# ------------------------------------------------------------ (de)serialise --
def _to_json(meta: WavMetadata) -> str:
    return json.dumps(
        {
            "file_path": str(meta.file_path),
            "file_name": meta.file_name,
            "duration_seconds": meta.duration_seconds,
            "sample_rate": meta.sample_rate,
            "channels": meta.channels,
            "bit_depth": meta.bit_depth,
            "tags": meta.tags,
        }
    )


def _from_json(raw: str) -> WavMetadata:
    data = json.loads(raw)
    return WavMetadata(
        file_path=Path(data["file_path"]),
        file_name=data["file_name"],
        duration_seconds=float(data["duration_seconds"]),
        sample_rate=int(data["sample_rate"]),
        channels=int(data["channels"]),
        bit_depth=int(data["bit_depth"]),
        tags=dict(data.get("tags") or {}),
    )
