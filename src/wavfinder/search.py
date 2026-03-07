from rapidfuzz import fuzz, process

from wavfinder.models import WavMetadata


class FuzzySearchEngine:
    """In-memory fuzzy search over WAV metadata."""

    def __init__(self) -> None:
        self._entries: list[WavMetadata] = []
        self._corpus: list[str] = []  # parallel list of searchable strings

    def set_entries(self, entries: list[WavMetadata]) -> None:
        """Replace the full index."""
        self._entries = list(entries)
        self._corpus = [e.searchable_text for e in self._entries]

    def add_entry(self, entry: WavMetadata) -> None:
        """Append a single entry (used during incremental scan)."""
        self._entries.append(entry)
        self._corpus.append(entry.searchable_text)

    @property
    def entries(self) -> list[WavMetadata]:
        return self._entries

    def search(
        self, query: str, limit: int = 50, score_cutoff: float = 30.0
    ) -> list[tuple[WavMetadata, float]]:
        """Return up to *limit* entries matching *query*, ranked by score.

        Each result is a (WavMetadata, score) tuple where score is 0-100.
        """
        if not query.strip():
            return [(e, 100.0) for e in self._entries[:limit]]

        results = process.extract(
            query,
            self._corpus,
            scorer=fuzz.WRatio,
            limit=limit,
            score_cutoff=score_cutoff,
        )
        # results: list of (match_string, score, index)
        return [(self._entries[idx], score) for _, score, idx in results]
