import logging
import os
from collections.abc import Iterable, Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

# Directories that appear on macOS/Windows volumes and never contain library
# audio. Walking them wastes minutes on a multi-terabyte drive.
SKIP_DIRS = {
    ".Trashes",
    ".Spotlight-V100",
    ".fseventsd",
    ".DocumentRevisions-V100",
    ".TemporaryItems",
    "$RECYCLE.BIN",
    "System Volume Information",
}


def is_wav(name: str) -> bool:
    """True for any case spelling of a .wav filename, excluding AppleDouble."""
    if name.startswith("._"):
        # macOS resource-fork stubs. They carry a .wav name but no audio, and
        # they are everywhere on libraries that have crossed an exFAT drive.
        return False
    return name.lower().endswith(".wav")


def scan_wav_files(roots: Iterable[Path]) -> Iterator[Path]:
    """Recursively yield every .wav file under each root in *roots*.

    Symlinks are followed, but a directory is only ever walked once, so a
    library containing a link back to one of its own parents cannot loop. Files
    reachable from more than one root are yielded once.
    """
    visited_dirs: set[tuple[int, int]] = set()
    seen_files: set[str] = set()

    for root in roots:
        for dirpath, dirnames, filenames in os.walk(
            root, followlinks=True, onerror=_log_walk_error
        ):
            key = _dir_key(dirpath)
            if key is not None:
                if key in visited_dirs:
                    dirnames[:] = []
                    continue
                visited_dirs.add(key)

            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

            for name in filenames:
                if not is_wav(name):
                    continue
                path = os.path.join(dirpath, name)
                real = os.path.realpath(path)
                if real in seen_files:
                    continue
                seen_files.add(real)
                yield Path(path)


def _dir_key(dirpath: str) -> tuple[int, int] | None:
    """Identify a directory by (device, inode) so symlinks cannot fool us."""
    try:
        st = os.stat(dirpath)
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _log_walk_error(error: OSError) -> None:
    # Unreadable directories are normal on shared drives; note and carry on.
    logger.debug("Skipping %s: %s", error.filename, error)
