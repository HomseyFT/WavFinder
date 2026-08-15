from pathlib import Path

import pytest

from tests.conftest import make_wav
from wavfinder.fileops import (
    CollisionError,
    find_sidecars,
    transfer,
    unique_destination,
)


@pytest.fixture
def src_and_dest(tmp_path: Path) -> tuple[Path, Path]:
    source = make_wav(tmp_path / "lib" / "clip.wav")
    dest = tmp_path / "dest"
    dest.mkdir()
    return source, dest


def test_move(src_and_dest):
    source, dest = src_and_dest
    result = transfer(source, dest)
    assert result.destination == dest / "clip.wav"
    assert result.destination.is_file()
    assert not source.exists()


def test_copy_leaves_the_original(src_and_dest):
    source, dest = src_and_dest
    result = transfer(source, dest, copy=True)
    assert result.copied is True
    assert source.is_file()
    assert result.destination.is_file()


def test_collision_raises_so_the_caller_can_ask(src_and_dest):
    source, dest = src_and_dest
    (dest / "clip.wav").write_bytes(b"existing")
    with pytest.raises(CollisionError):
        transfer(source, dest)
    assert source.is_file(), "a refused transfer must not touch the source"


def test_collision_rename_keeps_both(src_and_dest):
    source, dest = src_and_dest
    (dest / "clip.wav").write_bytes(b"existing")
    result = transfer(source, dest, rename_on_collision=True)
    assert result.destination.name == "clip (1).wav"
    assert (dest / "clip.wav").read_bytes() == b"existing"


def test_collision_overwrite(src_and_dest):
    source, dest = src_and_dest
    (dest / "clip.wav").write_bytes(b"existing")
    result = transfer(source, dest, overwrite=True)
    assert result.destination.read_bytes() != b"existing"


def test_unique_destination_counts_up(tmp_path: Path):
    (tmp_path / "a.wav").write_bytes(b"")
    (tmp_path / "a (1).wav").write_bytes(b"")
    assert unique_destination(tmp_path / "a.wav").name == "a (2).wav"


def test_sidecars_are_found_and_carried(src_and_dest):
    source, dest = src_and_dest
    apple = source.with_name("._clip.wav")
    apple.write_bytes(b"\x00")
    peaks = source.with_name("clip.wav.reapeaks")
    peaks.write_bytes(b"\x00")

    assert set(find_sidecars(source)) == {apple, peaks}

    result = transfer(source, dest)
    assert len(result.sidecars) == 2
    assert not apple.exists() and not peaks.exists()
    assert (dest / "._clip.wav").is_file()


def test_sidecars_can_be_left_behind(src_and_dest):
    source, dest = src_and_dest
    apple = source.with_name("._clip.wav")
    apple.write_bytes(b"\x00")
    result = transfer(source, dest, include_sidecars=False)
    assert result.sidecars == []
    assert apple.is_file()


def test_unrelated_file_sharing_a_stem_is_left_alone(src_and_dest):
    source, dest = src_and_dest
    stranger = source.with_name("clip.txt")
    stranger.write_text("someone's notes")
    transfer(source, dest)
    assert stranger.is_file()


def test_same_directory_is_rejected(src_and_dest):
    source, _dest = src_and_dest
    with pytest.raises(ValueError):
        transfer(source, source.parent)


def test_missing_source(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        transfer(tmp_path / "nope.wav", tmp_path)


def test_missing_destination(src_and_dest):
    source, dest = src_and_dest
    with pytest.raises(NotADirectoryError):
        transfer(source, dest / "nope")
