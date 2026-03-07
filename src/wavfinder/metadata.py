import logging
import wave
from pathlib import Path

import mutagen

from wavfinder.models import WavMetadata

logger = logging.getLogger(__name__)


def extract_metadata(path: Path) -> WavMetadata | None:
    """Extract metadata from a .wav file. Returns None on failure."""
    try:
        # --- Technical properties via stdlib wave ---
        with wave.open(str(path), "rb") as wf:
            sample_rate = wf.getframerate()
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()  # bytes per sample
            n_frames = wf.getnframes()

        bit_depth = sample_width * 8
        duration = n_frames / sample_rate if sample_rate else 0.0

        # --- RIFF INFO tags via mutagen ---
        tags = _extract_tags(path)

        return WavMetadata(
            file_path=path.resolve(),
            file_name=path.name,
            duration_seconds=round(duration, 3),
            sample_rate=sample_rate,
            channels=channels,
            bit_depth=bit_depth,
            tags=tags,
        )
    except Exception:
        logger.warning("Failed to read %s", path, exc_info=True)
        return None


def _extract_tags(path: Path) -> dict[str, str]:
    """Read RIFF INFO and other tags via mutagen."""
    tags: dict[str, str] = {}
    try:
        audio = mutagen.File(str(path))
        if audio is None or audio.tags is None:
            return tags
        for key, value in audio.tags.items():
            # mutagen returns values as lists or strings depending on format
            if isinstance(value, list):
                tags[str(key)] = ", ".join(str(v) for v in value)
            else:
                tags[str(key)] = str(value)
    except Exception:
        logger.debug("No tags found for %s", path, exc_info=True)
    return tags
