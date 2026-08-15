import os
from pathlib import Path

import pytest

from tests.conftest import make_wav
from wavfinder.scanner import is_wav, scan_wav_files


@pytest.mark.parametrize(
    "name,expected",
    [
        ("a.wav", True),
        ("A.WAV", True),
        ("mixed.Wav", True),
        ("._a.wav", False),  # macOS AppleDouble stub
        ("a.wave", False),
        ("a.aiff", False),
        ("wav", False),
    ],
)
def test_is_wav(name: str, expected: bool):
    assert is_wav(name) is expected


def test_finds_every_case_spelling(library: Path):
    names = {p.name for p in scan_wav_files([library])}
    assert "car_door_slam.wav" in names
    assert "CAR_HORN.WAV" in names


def test_skips_appledouble_and_non_wav(library: Path):
    names = {p.name for p in scan_wav_files([library])}
    assert "._car_door_slam.wav" not in names
    assert "notes.txt" not in names


def test_skips_system_directories(tmp_path: Path):
    make_wav(tmp_path / ".Trashes" / "deleted.wav")
    make_wav(tmp_path / "keep.wav")
    names = {p.name for p in scan_wav_files([tmp_path])}
    assert names == {"keep.wav"}


def test_multiple_roots(tmp_path: Path):
    make_wav(tmp_path / "one" / "a.wav")
    make_wav(tmp_path / "two" / "b.wav")
    names = {p.name for p in scan_wav_files([tmp_path / "one", tmp_path / "two"])}
    assert names == {"a.wav", "b.wav"}


def test_overlapping_roots_yield_each_file_once(tmp_path: Path):
    make_wav(tmp_path / "outer" / "inner" / "a.wav")
    found = list(scan_wav_files([tmp_path / "outer", tmp_path / "outer" / "inner"]))
    assert len(found) == 1


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges")
def test_symlink_loop_terminates(tmp_path: Path):
    root = tmp_path / "lib"
    make_wav(root / "a.wav")
    (root / "loop").symlink_to(root, target_is_directory=True)
    found = list(scan_wav_files([root]))
    assert len(found) == 1


def test_missing_root_is_not_an_error(tmp_path: Path):
    assert list(scan_wav_files([tmp_path / "does_not_exist"])) == []
