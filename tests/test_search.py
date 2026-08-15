from pathlib import Path

import pytest

from wavfinder.models import WavMetadata
from wavfinder.search import (
    FuzzySearchEngine,
    find_match_spans,
    split_terms,
    term_threshold,
)


def entry(name: str, description: str = "", parent: str = "/lib") -> WavMetadata:
    return WavMetadata(
        file_path=Path(parent) / name,
        file_name=name,
        duration_seconds=1.0,
        sample_rate=48000,
        channels=2,
        bit_depth=24,
        tags={"Description": description} if description else {},
    )


@pytest.fixture
def engine() -> FuzzySearchEngine:
    eng = FuzzySearchEngine()
    eng.add_entries(
        [
            entry("car_door.wav", "Car door slam heavy"),
            entry("car_horn.wav", "Car horn honk"),
            entry("dog_bark.wav", "Dog bark large breed"),
            entry("RAIN.wav", "Rain on tin roof"),
        ]
    )
    return eng


def test_empty_query_returns_everything(engine: FuzzySearchEngine):
    outcome = engine.search("")
    assert len(outcome.results) == 4
    assert outcome.truncated is False


def test_finds_by_description(engine: FuzzySearchEngine):
    names = [r.entry.file_name for r in engine.search("horn").results]
    assert names[0] == "car_horn.wav"


def test_technical_fields_are_not_searchable(engine: FuzzySearchEngine):
    """Sample rate and bit depth must stay out of the corpus."""
    assert "48000" not in engine.entries[0].searchable_text
    assert "24bit" not in engine.entries[0].searchable_text


def test_case_insensitive_by_default(engine: FuzzySearchEngine):
    assert any(r.entry.file_name == "RAIN.wav" for r in engine.search("rain").results)


def test_case_sensitive_search_excludes_the_wrong_case(engine: FuzzySearchEngine):
    insensitive = engine.search("DOG", case_sensitive=False).results
    assert [r.entry.file_name for r in insensitive] == ["dog_bark.wav"]

    sensitive = engine.search("DOG", case_sensitive=True).results
    assert sensitive == [], "uppercase DOG should not match lowercase dog"


def test_case_sensitive_search_still_finds_an_exact_case_match(engine: FuzzySearchEngine):
    results = engine.search("Dog", case_sensitive=True).results
    assert [r.entry.file_name for r in results] == ["dog_bark.wav"]


def test_a_single_term_does_not_return_the_whole_library(engine: FuzzySearchEngine):
    """A loose scorer alone hands back everything, ranked. It must not."""
    names = [r.entry.file_name for r in engine.search("horn").results]
    assert "dog_bark.wav" not in names
    assert names[0] == "car_horn.wav"


def test_every_term_must_match(engine: FuzzySearchEngine):
    """'car door' means car AND door, not car OR door."""
    names = [r.entry.file_name for r in engine.search("car door").results]
    assert names == ["car_door.wav"]


def test_a_term_matching_nothing_yields_nothing(engine: FuzzySearchEngine):
    assert engine.search("xylophone").results == []
    assert engine.search("car xylophone").results == []


@pytest.mark.parametrize(
    "typo,expected",
    [
        ("hron", "car_horn.wav"),  # transposed
        ("brk", None),  # too short to guess from
        ("barrk", "dog_bark.wav"),  # doubled letter
    ],
)
def test_typo_tolerance(engine: FuzzySearchEngine, typo: str, expected):
    names = [r.entry.file_name for r in engine.search(typo).results]
    if expected is None:
        assert names == []
    else:
        assert names[0] == expected


def test_short_terms_must_be_exact(engine: FuzzySearchEngine):
    """Three letters carry too little signal to fuzzy-match safely."""
    assert term_threshold("car") == 100.0
    assert term_threshold("thunder") < 100.0
    assert engine.search("cat").results == []


def test_truncated_flag(engine: FuzzySearchEngine):
    assert engine.search("", limit=2).truncated is True
    assert engine.search("", limit=99).truncated is False


def test_root_filter(engine: FuzzySearchEngine):
    engine.add_entries([entry("car_extra.wav", "Car extra", parent="/other")])
    results = engine.search("car", root="/other").results
    assert [r.entry.file_name for r in results] == ["car_extra.wav"]


def test_clear_empties_the_index(engine: FuzzySearchEngine):
    engine.clear()
    assert len(engine) == 0
    assert engine.search("car").results == []


def test_replace_entry_reports_a_stranger(engine: FuzzySearchEngine):
    stranger = entry("never_added.wav", "nothing")
    assert engine.replace_entry(stranger, stranger) is False


def test_replace_entry_updates_path(engine: FuzzySearchEngine):
    eng = FuzzySearchEngine()
    original = entry("a.wav", "thing")
    eng.add_entry(original)
    moved = entry("a.wav", "thing", parent="/elsewhere")
    assert eng.replace_entry(original, moved) is True
    assert eng.entries[0].file_path == Path("/elsewhere/a.wav")


# ------------------------------------------------------------ highlighting --
def test_split_terms():
    assert split_terms("  car   door ") == ["car", "door"]


def test_spans_find_every_literal_occurrence():
    spans = find_match_spans("car door and car horn", ["car"])
    assert spans == [(0, 3), (13, 16)]


def test_spans_are_case_insensitive_by_default():
    assert find_match_spans("CAR door", ["car"]) == [(0, 3)]


def test_spans_respect_case_sensitivity():
    assert find_match_spans("CAR door", ["car"], case_sensitive=True) != [(0, 3)]


def test_spans_merge_when_terms_overlap():
    spans = find_match_spans("car door", ["car d", "door"])
    assert spans == [(0, 8)]


def test_spans_survive_a_typo():
    """A misspelled term should still land on the word it meant."""
    text = "a loud thunder clap"
    spans = find_match_spans(text, ["thundr"])
    assert spans, "a near-miss term should still highlight something"
    start, end = spans[0]
    word_start, word_end = text.index("thunder"), text.index("thunder") + 7
    # The highlight need not be exact, but it must sit on the right word.
    assert start < word_end and end > word_start


def test_spans_ignore_a_term_that_matches_nothing():
    assert find_match_spans("car door slam", ["xylophone"]) == []


def test_no_terms_no_spans():
    assert find_match_spans("car door", []) == []
    assert find_match_spans("", ["car"]) == []
