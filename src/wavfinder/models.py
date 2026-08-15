from dataclasses import dataclass, field
from pathlib import Path

# Tag keys that describe *what the sound is*, in the order we prefer them when
# picking a single headline description for a file.
DESCRIPTION_KEYS = (
    "Description",
    "Title",
    "Subject",
    "Category",
    "Keywords",
    "Comment",
)

# Tags that say something about the sound, as opposed to who published it.
# Only these are searched and shown.
#
# The distinction matters more than it looks: a commercial library stamps every
# single file with the same copyright line, web address and release date. Left
# in the search corpus they match everything, which is the same problem that
# sample rate and bit depth caused.
DESCRIPTIVE_KEYS = frozenset(
    {
        "Description",
        "Title",
        "Subject",
        "Category",
        "Subcategory",
        "Category ID",
        "Keywords",
        "Comment",
        "Designer",
        "Library",
        "Microphone",
        "Scene",
        "Project",
        "Album",
        "Artist",
    }
)


@dataclass
class WavMetadata:
    """Metadata extracted from a single .wav file."""

    file_path: Path
    file_name: str
    duration_seconds: float
    sample_rate: int
    channels: int
    bit_depth: int
    # Human-readable descriptive tags, e.g. {"Description": "car door slam"}.
    tags: dict[str, str] = field(default_factory=dict)
    searchable_text: str = ""

    def __post_init__(self) -> None:
        if not self.searchable_text:
            self.searchable_text = self._build_searchable_text()

    @property
    def description(self) -> str:
        """The best single descriptive line for this file, or ''."""
        for key in DESCRIPTION_KEYS:
            value = self.tags.get(key, "").strip()
            if value:
                return value
        # Fall back to any tag at all rather than showing nothing.
        for value in self.tags.values():
            if value.strip():
                return value.strip()
        return ""

    @property
    def parent_dir(self) -> str:
        return str(self.file_path.parent)

    def descriptive_tags(self) -> dict[str, str]:
        """The tags worth showing and searching, with duplicates removed.

        Libraries routinely write the same sentence into bext, iXML and ID3, so
        without this the details pane repeats itself and the same words get
        counted several times over.
        """
        kept: dict[str, str] = {}
        seen: set[str] = set()
        for key, value in self.tags.items():
            if key not in DESCRIPTIVE_KEYS:
                continue
            text = value.strip()
            folded = text.casefold()
            if not text or folded in seen:
                continue
            seen.add(folded)
            kept[key] = text
        return kept

    def _build_searchable_text(self) -> str:
        """Concatenate the descriptive fields into a single searchable string.

        Deliberately excludes sample rate, channel count and bit depth: they are
        the same across most of a library, so including them drags every entry
        toward the same fuzzy score and buries the real matches. Publisher and
        copyright fields are left out for the same reason.
        """
        parts = [self.file_name, self.parent_dir]
        parts.extend(self.descriptive_tags().values())
        return " | ".join(parts)

    def format_duration(self) -> str:
        """Return a human-readable duration string."""
        m, s = divmod(self.duration_seconds, 60)
        if m:
            return f"{int(m)}m {s:.1f}s"
        return f"{s:.1f}s"

    def description_summary(self, max_len: int = 80) -> str:
        """Return a truncated description for table display."""
        text = self.description
        if len(text) > max_len:
            return text[: max_len - 1] + "…"
        return text
