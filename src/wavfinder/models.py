from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WavMetadata:
    """Metadata extracted from a single .wav file."""

    file_path: Path
    file_name: str
    duration_seconds: float
    sample_rate: int
    channels: int
    bit_depth: int
    tags: dict[str, str] = field(default_factory=dict)
    searchable_text: str = ""

    def __post_init__(self) -> None:
        if not self.searchable_text:
            self.searchable_text = self._build_searchable_text()

    def _build_searchable_text(self) -> str:
        """Concatenate all metadata fields into a single searchable string."""
        parts = [
            self.file_name,
            f"{self.duration_seconds:.1f}s",
            f"{self.sample_rate}Hz",
            f"{self.channels}ch",
            f"{self.bit_depth}bit",
        ]
        for key, value in self.tags.items():
            parts.append(f"{key}: {value}")
        return " | ".join(parts)

    def format_duration(self) -> str:
        """Return a human-readable duration string."""
        m, s = divmod(self.duration_seconds, 60)
        if m:
            return f"{int(m)}m {s:.1f}s"
        return f"{s:.1f}s"

    def tags_summary(self, max_len: int = 60) -> str:
        """Return a truncated summary of tags for table display."""
        if not self.tags:
            return ""
        summary = ", ".join(f"{k}={v}" for k, v in self.tags.items())
        if len(summary) > max_len:
            return summary[: max_len - 1] + "…"
        return summary
