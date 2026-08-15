import json
from pathlib import Path

import pytest

from wavfinder import config as config_module


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch):
    """Never touch the real user config while testing."""
    monkeypatch.setattr(config_module, "config_dir", lambda: tmp_path / "cfg")


def test_defaults_when_nothing_saved():
    cfg = config_module.load()
    assert cfg.libraries == []
    assert cfg.case_sensitive is False


def test_round_trip(tmp_path: Path):
    cfg = config_module.Config()
    cfg.add_library(tmp_path)
    cfg.case_sensitive = True
    config_module.save(cfg)

    loaded = config_module.load()
    assert loaded.case_sensitive is True
    assert loaded.libraries[0].path == str(tmp_path.resolve())


def test_duplicate_libraries_are_rejected(tmp_path: Path):
    cfg = config_module.Config()
    assert cfg.add_library(tmp_path) is True
    assert cfg.add_library(tmp_path) is False
    assert len(cfg.libraries) == 1


def test_enabled_paths_respects_the_toggle(tmp_path: Path):
    cfg = config_module.Config()
    cfg.add_library(tmp_path)
    resolved = str(tmp_path.resolve())
    cfg.set_enabled(resolved, False)
    assert cfg.enabled_paths() == []
    cfg.set_enabled(resolved, True)
    assert cfg.enabled_paths() == [Path(resolved)]


def test_remove_library(tmp_path: Path):
    cfg = config_module.Config()
    cfg.add_library(tmp_path)
    cfg.remove_library(str(tmp_path.resolve()))
    assert cfg.libraries == []


def test_corrupt_config_falls_back_to_defaults():
    path = config_module.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json")
    assert config_module.load().libraries == []


def test_entries_without_a_path_are_dropped():
    path = config_module.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"libraries": [{"enabled": True}, {"path": "/a"}]}))
    assert [lib.path for lib in config_module.load().libraries] == ["/a"]
