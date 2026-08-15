from pathlib import Path

import pytest

from tests.conftest import (
    WAVE_FORMAT_EXTENSIBLE,
    WAVE_FORMAT_IEEE_FLOAT,
    make_wav,
)
from wavfinder.metadata import extract_metadata


def test_reads_pcm_technical_fields(tmp_path: Path):
    path = make_wav(tmp_path / "a.wav", rate=44100, channels=1, bits=16, frames=44100)
    meta = extract_metadata(path)
    assert meta is not None
    assert (meta.sample_rate, meta.channels, meta.bit_depth) == (44100, 1, 16)
    assert meta.duration_seconds == pytest.approx(1.0)


def test_reads_float_wav_that_stdlib_wave_rejects(tmp_path: Path):
    """The regression this parser exists for: float files were dropped."""
    path = make_wav(
        tmp_path / "float.wav", fmt_tag=WAVE_FORMAT_IEEE_FLOAT, bits=32, frames=48000
    )
    import wave

    with pytest.raises(wave.Error):
        wave.open(str(path), "rb")

    meta = extract_metadata(path)
    assert meta is not None
    assert meta.bit_depth == 32
    assert meta.duration_seconds == pytest.approx(1.0)


def test_reads_extensible_wav(tmp_path: Path):
    path = make_wav(
        tmp_path / "ext.wav", fmt_tag=WAVE_FORMAT_EXTENSIBLE, bits=24, frames=48000
    )
    meta = extract_metadata(path)
    assert meta is not None
    assert meta.bit_depth == 24
    assert meta.duration_seconds == pytest.approx(1.0)


def test_reads_bext_description(tmp_path: Path):
    path = make_wav(tmp_path / "b.wav", bext_description="Glass smash, large pane")
    meta = extract_metadata(path)
    assert meta is not None
    assert meta.tags["Description"] == "Glass smash, large pane"
    assert meta.description == "Glass smash, large pane"


def test_reads_riff_info_with_readable_keys(tmp_path: Path):
    path = make_wav(tmp_path / "c.wav", info={"INAM": "Dog bark", "IGNR": "Animals"})
    meta = extract_metadata(path)
    assert meta is not None
    assert meta.tags["Title"] == "Dog bark"
    assert meta.tags["Category"] == "Animals"


def test_reads_ixml(tmp_path: Path):
    path = make_wav(
        tmp_path / "d.wav",
        ixml="<BWFXML><USER><DESCRIPTION>Thunder clap</DESCRIPTION>"
        "<CATEGORY>Weather</CATEGORY></USER></BWFXML>",
    )
    meta = extract_metadata(path)
    assert meta is not None
    assert meta.tags["Description"] == "Thunder clap"
    assert meta.tags["Category"] == "Weather"


def test_odd_sized_chunk_padding_is_handled(tmp_path: Path):
    """An odd-length INFO value must not knock the chunk walk out of alignment."""
    path = make_wav(
        tmp_path / "odd.wav", info={"INAM": "odd"}, bext_description="still readable"
    )
    meta = extract_metadata(path)
    assert meta is not None
    assert meta.tags["Title"] == "odd"
    assert meta.sample_rate == 48000


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not a wav at all",
        b"RIFF\x10\x00\x00\x00WAVEfmt ",  # truncated
        b"RIFF\x10\x00\x00\x00AVI LIST",  # RIFF but not WAVE
    ],
)
def test_unreadable_files_return_none(tmp_path: Path, content: bytes):
    path = tmp_path / "bad.wav"
    path.write_bytes(content)
    assert extract_metadata(path) is None


def test_missing_file_returns_none(tmp_path: Path):
    assert extract_metadata(tmp_path / "nope.wav") is None


def test_bad_ixml_does_not_lose_the_file(tmp_path: Path):
    """One malformed optional chunk must not cost us the whole entry."""
    path = make_wav(tmp_path / "e.wav", ixml="<BWFXML><UNCLOSED>", frames=48000)
    meta = extract_metadata(path)
    assert meta is not None
    assert meta.duration_seconds == pytest.approx(1.0)


def test_data_payload_is_not_read_into_memory(tmp_path: Path, monkeypatch):
    """Guard the seek-past-data behaviour: libraries hold multi-GB files."""
    path = make_wav(tmp_path / "big.wav", frames=200000)
    reads: list[int] = []
    real_open = open

    class CountingFile:
        def __init__(self, fh):
            self._fh = fh

        def read(self, n=-1):
            reads.append(n)
            return self._fh.read(n)

        # Dunder lookups skip __getattr__, so the context manager needs these.
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self._fh, name)

    def fake_open(*args, **kwargs):
        return CountingFile(real_open(*args, **kwargs))

    monkeypatch.setattr("builtins.open", fake_open)
    meta = extract_metadata(path)
    assert meta is not None
    assert max(reads) < 200000 * 4
