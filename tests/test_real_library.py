"""Behaviour checked against the shape of a real commercial library file.

Modelled on a Sound Ideas / Warner Bros. SFX Library wav: a 24-bit BWF whose
description is repeated in bext, iXML and ID3, stamped with the same copyright
line as every other file in the library, and carrying a ~70 KB proprietary
Soundminer chunk after the audio.
"""

import builtins
from pathlib import Path

import pytest

from tests.conftest import make_sound_ideas_wav
from wavfinder.metadata import extract_metadata
from wavfinder.search import FuzzySearchEngine

DESCRIPTION = "BIRD, FANTASY - GIANT HUMMINGBIRD HOVERING, CARTOON"


@pytest.fixture
def sound_ideas_file(tmp_path: Path) -> Path:
    # A fixed folder name: the file's own path is part of the search corpus, so
    # pytest's generated directory names would otherwise leak into match tests.
    return make_sound_ideas_wav(
        tmp_path / "WB" / "BirdFantasy WB04_04_2.wav", DESCRIPTION
    )


def test_description_comes_from_bext(sound_ideas_file: Path):
    meta = extract_metadata(sound_ideas_file)
    assert meta is not None
    assert meta.description == DESCRIPTION


def test_technical_fields(sound_ideas_file: Path):
    meta = extract_metadata(sound_ideas_file)
    assert (meta.sample_rate, meta.channels, meta.bit_depth) == (48000, 2, 24)


def test_publisher_boilerplate_is_not_searchable(sound_ideas_file: Path):
    """Every file in a commercial library carries the same copyright stamp.

    Left in the corpus it matches everything, which is worse than useless.
    """
    meta = extract_metadata(sound_ideas_file)
    assert "Sound Ideas" not in meta.searchable_text
    assert "2007-03-16" not in meta.searchable_text
    assert DESCRIPTION in meta.searchable_text


def test_repeated_description_is_shown_only_once(sound_ideas_file: Path):
    """bext and iXML both carry it; the details pane must not say it twice."""
    meta = extract_metadata(sound_ideas_file)
    values = list(meta.descriptive_tags().values())
    assert values.count(DESCRIPTION) == 1


def test_proprietary_chunks_are_not_read(sound_ideas_file: Path):
    """The 70 KB Soundminer blob must be seeked past, not loaded."""
    total = 0
    real_open = builtins.open

    class Counting:
        def __init__(self, fh):
            self._fh = fh

        def read(self, n=-1):
            nonlocal total
            data = self._fh.read(n)
            total += len(data)
            return data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self._fh, name)

    builtins.open = lambda *a, **k: Counting(real_open(*a, **k))
    try:
        assert extract_metadata(sound_ideas_file) is not None
    finally:
        builtins.open = real_open

    assert total < 10_000, f"read {total} bytes; the SMED chunk is being loaded"


@pytest.mark.parametrize(
    "query",
    ["hummingbird", "humming", "bird", "bird fantasy", "cartoon", "hummingbrd"],
)
def test_queries_that_should_find_it(sound_ideas_file: Path, query: str):
    engine = FuzzySearchEngine()
    engine.add_entry(extract_metadata(sound_ideas_file))
    assert engine.search(query).results, f"{query!r} should have matched"


@pytest.mark.parametrize("query", ["sound", "ideas", "2007", "www", "dog"])
def test_queries_that_should_not_find_it(sound_ideas_file: Path, query: str):
    engine = FuzzySearchEngine()
    engine.add_entry(extract_metadata(sound_ideas_file))
    assert engine.search(query).results == [], f"{query!r} should not have matched"


def test_all_caps_descriptions_and_the_case_toggle(sound_ideas_file: Path):
    """These libraries write descriptions in capitals.

    Case-insensitive search (the default) finds them either way; with Match case
    on, a lowercase query genuinely will not match, which is the point of the
    toggle rather than a bug.
    """
    engine = FuzzySearchEngine()
    engine.add_entry(extract_metadata(sound_ideas_file))
    assert engine.search("bird", case_sensitive=False).results
    assert engine.search("BIRD", case_sensitive=True).results
    assert engine.search("hovering", case_sensitive=True).results == []
