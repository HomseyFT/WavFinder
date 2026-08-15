"""End-to-end checks that drive the real widgets without entering mainloop.

Skipped automatically where there is no display (CI, a headless build box).
"""

from pathlib import Path

import pytest

from wavfinder import config as config_module

tk = pytest.importorskip("tkinter")


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config_module, "config_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(
        "wavfinder.index_cache.config_dir", lambda: tmp_path / "cfg", raising=False
    )


@pytest.fixture
def app(library: Path, isolated_config):
    try:
        probe = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    probe.destroy()

    from wavfinder.app import WavFinderApp

    application = WavFinderApp([library])
    yield application
    application._on_close()


def pump(app, cycles: int = 40) -> None:
    """Let queued callbacks and worker results land, without a mainloop."""
    import time

    for _ in range(cycles):
        app.window.update()
        time.sleep(0.02)


def test_app_indexes_the_library_and_fills_the_table(app):
    pump(app)
    assert len(app.engine) == 4
    assert len(app.tree.get_children()) == 4


def test_searching_narrows_the_table(app):
    pump(app)
    app._search_var.set("horn")
    pump(app)
    shown = [app.tree.set(iid, "filename") for iid in app.tree.get_children()]
    assert shown == ["CAR_HORN.WAV"]


def test_selecting_a_row_highlights_the_terms_in_the_preview(app):
    pump(app)
    app._search_var.set("rain")
    pump(app)
    first = app.tree.get_children()[0]
    app.tree.selection_set(first)
    pump(app, cycles=5)

    ranges = app._preview_text.tag_ranges("match")
    assert ranges, "the searched term should be highlighted in the details pane"
    start, end = ranges[0], ranges[1]
    assert app._preview_text.get(start, end).lower() == "rain"


def test_case_toggle_changes_the_results(app):
    pump(app)
    app._search_var.set("CAR")
    pump(app)
    assert len(app.tree.get_children()) >= 1

    app._case_var.set(True)
    app._on_case_toggled()
    pump(app)
    shown = [app.tree.set(iid, "filename") for iid in app.tree.get_children()]
    # Only the uppercase-named file still matches a case-sensitive "CAR".
    assert "car_door_slam.wav" not in shown


def test_sorting_by_length_is_numeric_not_alphabetical(app):
    pump(app)
    app._sort_column("duration")
    lengths = [app.tree.set(iid, "duration") for iid in app.tree.get_children()]
    # 0.5s, 2.0s, 3.0s, 10.0s -- an alphabetical sort would put 10.0s second.
    assert lengths == ["0.5s", "2.0s", "3.0s", "10.0s"]


def test_table_shows_only_the_requested_columns(app):
    assert app._columns == ("filename", "duration", "description", "path")


def test_moving_a_file_updates_the_index_in_place(app, tmp_path, monkeypatch):
    pump(app)
    destination = tmp_path / "picked"
    destination.mkdir()
    monkeypatch.setattr(
        "wavfinder.app.filedialog.askdirectory", lambda **kwargs: str(destination)
    )

    app._search_var.set("horn")
    pump(app)
    app.tree.selection_set(app.tree.get_children()[0])
    pump(app, cycles=5)
    original = app._current_meta()
    assert original is not None

    app._move_selected()
    pump(app, cycles=5)

    assert (destination / "CAR_HORN.WAV").is_file()
    assert not original.file_path.exists()
    # The entry follows the file rather than vanishing from the results.
    moved = [e for e in app.engine.entries if e.file_name == "CAR_HORN.WAV"]
    assert len(moved) == 1
    assert moved[0].file_path.parent == destination.resolve()


def test_copying_leaves_the_original_in_place(app, tmp_path, monkeypatch):
    pump(app)
    destination = tmp_path / "copies"
    destination.mkdir()
    monkeypatch.setattr(
        "wavfinder.app.filedialog.askdirectory", lambda **kwargs: str(destination)
    )

    app._search_var.set("horn")
    pump(app)
    app.tree.selection_set(app.tree.get_children()[0])
    pump(app, cycles=5)
    original = app._current_meta()

    app._copy_selected()
    pump(app, cycles=5)

    assert (destination / "CAR_HORN.WAV").is_file()
    assert original.file_path.is_file(), "a copy must not remove the original"


def test_descriptions_reach_the_table(app):
    pump(app)
    descriptions = {app.tree.set(iid, "description") for iid in app.tree.get_children()}
    assert "Car door slam, heavy, exterior" in descriptions
    assert "Forest birdsong, morning" in descriptions
