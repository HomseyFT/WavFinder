from pathlib import Path
from typing import Generator


def scan_wav_files(root: Path) -> Generator[Path, None, None]:
    """Recursively yield all .wav files under *root*."""
    yield from root.rglob("*.wav")
    # Also catch .WAV (case-insensitive on case-sensitive filesystems)
    yield from (p for p in root.rglob("*.WAV") if p.suffix == ".WAV")
