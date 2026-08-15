import threading
from pathlib import Path

from tests.conftest import make_wav
from wavfinder.index_cache import IndexCache
from wavfinder.indexer import reindex_file, scan_library
from wavfinder.models import WavMetadata


def collect(roots, **kwargs) -> tuple[list[WavMetadata], object]:
    found: list[WavMetadata] = []
    stats = scan_library(roots, found.extend, **kwargs)
    return found, stats


def test_scan_indexes_every_readable_wav(library: Path):
    found, stats = collect([library])
    names = {m.file_name for m in found}
    assert names == {
        "car_door_slam.wav",
        "CAR_HORN.WAV",
        "forest_birds.wav",
        "rain_heavy.wav",
    }
    assert stats.indexed == 4
    assert stats.unreadable == 1  # truncated.wav


def test_durations_are_read_correctly(library: Path):
    found, _ = collect([library])
    by_name = {m.file_name: m for m in found}
    assert round(by_name["car_door_slam.wav"].duration_seconds) == 2
    assert round(by_name["forest_birds.wav"].duration_seconds) == 10


def test_second_scan_comes_from_the_cache(library: Path, tmp_path: Path):
    cache = IndexCache(tmp_path / "cache.sqlite3")
    _, first = collect([library], cache=cache)
    assert first.from_cache == 0

    _, second = collect([library], cache=cache)
    assert second.from_cache == 4
    assert second.indexed == 4
    cache.close()


def test_cache_is_invalidated_when_a_file_changes(library: Path, tmp_path: Path):
    cache = IndexCache(tmp_path / "cache.sqlite3")
    collect([library], cache=cache)

    target = library / "Vehicles" / "car_door_slam.wav"
    make_wav(target, bext_description="Rewritten description", frames=48000)

    found, stats = collect([library], cache=cache)
    assert stats.from_cache == 3
    by_name = {m.file_name: m for m in found}
    assert by_name["car_door_slam.wav"].description == "Rewritten description"
    cache.close()


def test_deleted_files_are_pruned_from_the_cache(library: Path, tmp_path: Path):
    cache = IndexCache(tmp_path / "cache.sqlite3")
    collect([library], cache=cache)
    (library / "Vehicles" / "CAR_HORN.WAV").unlink()

    _, stats = collect([library], cache=cache)
    assert stats.pruned == 1
    cache.close()


def test_pruning_leaves_other_libraries_alone(library: Path, tmp_path: Path):
    """Scanning one library must not wipe the cache for another."""
    other = tmp_path / "other"
    make_wav(other / "solo.wav")
    cache = IndexCache(tmp_path / "cache.sqlite3")

    collect([library, other], cache=cache)
    _, stats = collect([library], cache=cache)  # only the first library
    assert stats.pruned == 0

    _, stats = collect([other], cache=cache)
    assert stats.from_cache == 1  # still cached
    cache.close()


def test_cancel_stops_the_scan(library: Path):
    cancel = threading.Event()
    cancel.set()
    found, stats = collect([library], cancel=cancel)
    assert stats.cancelled is True
    assert found == []


def test_cancelled_scan_does_not_prune(library: Path, tmp_path: Path):
    cache = IndexCache(tmp_path / "cache.sqlite3")
    collect([library], cache=cache)
    cancel = threading.Event()
    cancel.set()
    _, stats = collect([library], cache=cache, cancel=cancel)
    assert stats.pruned == 0

    _, stats = collect([library], cache=cache)
    assert stats.from_cache == 4, "a cancelled scan must not cost us the cache"
    cache.close()


def test_broken_cache_falls_back_to_parsing(library: Path, tmp_path: Path):
    unusable = tmp_path / "not_a_dir"
    unusable.write_text("this is a file, not a directory")
    cache = IndexCache(unusable / "cache.sqlite3")
    assert cache.available is False

    _, stats = collect([library], cache=cache)
    assert stats.indexed == 4, "a broken cache costs a rescan, not the app"


def test_reindex_file_after_a_move(library: Path, tmp_path: Path):
    cache = IndexCache(tmp_path / "cache.sqlite3")
    source = library / "Vehicles" / "car_door_slam.wav"
    destination = tmp_path / "moved.wav"
    destination.write_bytes(source.read_bytes())

    meta = reindex_file(destination, cache=cache)
    assert meta is not None
    assert meta.file_path == destination.resolve()
    cache.close()
