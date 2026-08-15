import re
import threading
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process, utils

from wavfinder.models import WavMetadata

# A term has to look this much like a stretch of the text before we highlight
# it. Low enough to survive a typo, high enough not to paint the whole line.
HIGHLIGHT_MIN_SCORE = 75.0

# Terms of this length or shorter must appear exactly. A typo in a three-letter
# word leaves too little signal to guess from, and loosening the bar there is
# what turns "car" into a match for half the library.
EXACT_TERM_LENGTH = 3

# Floor for the per-term bar, so a long word never demands near-perfection.
# 75 is the point where a single typo still matches a four- or five-letter word
# but two differences do not -- below it, "sound" starts matching "should".
TERM_SCORE_FLOOR = 75.0

# Words in the searchable text. Underscores and punctuation are separators, so
# "car_door_slam.wav" tokenises the same way its description does.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

# How many candidates to pull out of the fast C++ pass before applying the
# per-term test in Python. Wide enough that a good hit is not ranked out.
CANDIDATE_FACTOR = 5


@dataclass
class SearchResult:
    entry: WavMetadata
    score: float
    # The terms that produced this hit, for the preview pane to highlight.
    terms: list[str] = field(default_factory=list)


@dataclass
class SearchOutcome:
    results: list[SearchResult]
    # True when the index held more matches than *limit*, so the UI can say so
    # rather than quietly showing a partial list as if it were everything.
    truncated: bool = False


class FuzzySearchEngine:
    """In-memory fuzzy search over WAV metadata.

    Entries are appended by the scan thread while the UI thread searches, so
    reads run against an immutable snapshot rather than the live list.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[WavMetadata] = []
        self._corpus: list[str] = []  # parallel list of searchable strings
        # Snapshots keyed by root filter; None is the unfiltered whole index.
        self._snapshots: dict[str | None, tuple[list[WavMetadata], list[str]]] = {}

    def add_entry(self, entry: WavMetadata) -> None:
        """Append a single entry (used during incremental scan)."""
        self.add_entries((entry,))

    def add_entries(self, entries: "list[WavMetadata] | tuple[WavMetadata, ...]") -> None:
        """Append a batch of entries."""
        with self._lock:
            for entry in entries:
                self._entries.append(entry)
                self._corpus.append(entry.searchable_text)
            self._snapshots.clear()

    def clear(self) -> None:
        """Drop the whole index, e.g. when the user switches libraries."""
        with self._lock:
            self._entries = []
            self._corpus = []
            self._snapshots.clear()

    def replace_entry(self, old: WavMetadata, new: WavMetadata) -> bool:
        """Swap one entry in place, e.g. after a file is moved."""
        with self._lock:
            # Compare by identity: field-wise equality over a large index would
            # be needlessly slow, and two files can share every field but path.
            for idx, entry in enumerate(self._entries):
                if entry is old:
                    break
            else:
                return False
            self._entries[idx] = new
            self._corpus[idx] = new.searchable_text
            self._snapshots.clear()
            return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def entries(self) -> list[WavMetadata]:
        with self._lock:
            return list(self._entries)

    def _get_snapshot(
        self, root: str | None = None
    ) -> tuple[list[WavMetadata], list[str]]:
        """An immutable view of the index, optionally limited to one library.

        Filtering here rather than after the search matters: post-filtering a
        capped result list would silently drop matches that belong to the
        chosen library just because higher-scoring files elsewhere used up the
        cap first.
        """
        with self._lock:
            cached = self._snapshots.get(root)
            if cached is not None:
                return cached
            if root is None:
                snapshot = (list(self._entries), list(self._corpus))
            else:
                prefix = root.rstrip("/\\")
                entries, corpus = [], []
                for entry, text in zip(self._entries, self._corpus):
                    if str(entry.file_path).startswith(prefix):
                        entries.append(entry)
                        corpus.append(text)
                snapshot = (entries, corpus)
            # Only the current filter is worth keeping around.
            self._snapshots = {root: snapshot}
            return snapshot

    def search(
        self,
        query: str,
        limit: int = 200,
        score_cutoff: float = 30.0,
        case_sensitive: bool = False,
        root: str | None = None,
    ) -> SearchOutcome:
        """Return up to *limit* entries matching *query*, best first."""
        entries, corpus = self._get_snapshot(root)

        if not query.strip():
            shown = [SearchResult(entry=e, score=100.0) for e in entries[:limit]]
            return SearchOutcome(results=shown, truncated=len(entries) > limit)

        # default_process lowercases and strips punctuation; passing None leaves
        # the strings exactly as written, which is what case-sensitive means.
        processor = None if case_sensitive else utils.default_process
        terms = split_terms(query)

        # Two passes. The first is rapidfuzz's C++ scan, which is fast but very
        # forgiving -- on its own it will hand back the whole library, ranked,
        # for a query like "horn". The second pass keeps only the entries where
        # every term actually appears, which is what makes the result list short
        # enough to be useful.
        candidates = process.extract(
            query,
            corpus,
            scorer=fuzz.WRatio,
            processor=processor,
            limit=limit * CANDIDATE_FACTOR,
            score_cutoff=score_cutoff,
        )

        needles = [(t if case_sensitive else t.lower(), term_threshold(t)) for t in terms]

        scored: list[tuple[float, int]] = []
        for _, base_score, idx in candidates:
            words = tokenize(corpus[idx], case_sensitive=case_sensitive)
            term_scores = [term_score(needle, words) for needle, _ in needles]
            if any(score < bar for score, (_, bar) in zip(term_scores, needles)):
                continue
            # Rank on how well the terms matched, with the broad score breaking ties.
            scored.append((sum(term_scores) / len(term_scores) + base_score / 1000, idx))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        results = [
            SearchResult(entry=entries[idx], score=score, terms=terms)
            for score, idx in scored[:limit]
        ]
        return SearchOutcome(results=results, truncated=len(scored) > limit)


def tokenize(text: str, case_sensitive: bool = False) -> list[str]:
    """Split searchable text into the words a query term is compared against."""
    if not case_sensitive:
        text = text.lower()
    return _WORD_RE.findall(text)


def term_score(term: str, words: list[str]) -> float:
    """How well *term* matches the best single word in *words*, 0-100.

    Comparing against whole words rather than the raw string is what stops
    "horn" matching the "orn" inside "morning". A term that merely starts a
    longer word still counts as a full match, so "door" finds "doorway".
    """
    best = 0.0
    for word in words:
        if word.startswith(term):
            return 100.0
        score = fuzz.ratio(term, word)
        if score > best:
            best = score
    return best


def term_threshold(term: str) -> float:
    """How well *term* must match before we call it a hit.

    Scaled to the term's length so that roughly one typo is forgiven whatever
    the word: partial_ratio drops by about 1/len per wrong character, so a fixed
    threshold is simultaneously too strict for short words and too loose for
    long ones.
    """
    length = len(term)
    if length <= EXACT_TERM_LENGTH:
        return 100.0
    return max(TERM_SCORE_FLOOR, 100.0 * (length - 1.5) / length)


def split_terms(query: str) -> list[str]:
    """Split a query into the individual words we highlight."""
    return [term for term in query.split() if term]


def find_match_spans(
    text: str,
    terms: "list[str]",
    case_sensitive: bool = False,
    min_score: float = HIGHLIGHT_MIN_SCORE,
) -> list[tuple[int, int]]:
    """Locate each term inside *text*, returning merged (start, end) spans.

    Exact occurrences win: if a term appears literally we highlight every one of
    them. Only when there is no literal hit do we fall back to a fuzzy
    alignment, which finds the single closest stretch of text.
    """
    if not text or not terms:
        return []

    haystack = text if case_sensitive else text.lower()
    spans: list[tuple[int, int]] = []

    for term in terms:
        needle = term if case_sensitive else term.lower()
        found = _literal_spans(haystack, needle)
        if found:
            spans.extend(found)
            continue

        alignment = fuzz.partial_ratio_alignment(needle, haystack, score_cutoff=min_score)
        if alignment is not None and alignment.dest_end > alignment.dest_start:
            spans.append((alignment.dest_start, alignment.dest_end))

    return _merge_spans(spans)


def _literal_spans(haystack: str, needle: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = haystack.find(needle)
    while start != -1:
        spans.append((start, start + len(needle)))
        start = haystack.find(needle, start + len(needle))
    return spans


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Collapse overlapping or touching spans so tagging cannot double-apply."""
    if not spans:
        return []
    spans.sort()
    merged = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged
