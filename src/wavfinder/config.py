"""Remember the user's libraries and preferences between launches."""

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

APP_NAME = "WavFinder"


@dataclass
class Library:
    """One root directory the user wants searched."""

    path: str
    enabled: bool = True


@dataclass
class Config:
    libraries: list[Library] = field(default_factory=list)
    case_sensitive: bool = False
    last_move_dir: str = ""

    # ------------------------------------------------------------ helpers --
    def enabled_paths(self) -> list[Path]:
        return [Path(lib.path) for lib in self.libraries if lib.enabled]

    def add_library(self, path: Path) -> bool:
        """Add a library. Returns False if it was already present."""
        resolved = str(Path(path).expanduser().resolve())
        if any(lib.path == resolved for lib in self.libraries):
            return False
        self.libraries.append(Library(path=resolved))
        return True

    def remove_library(self, path: str) -> None:
        self.libraries = [lib for lib in self.libraries if lib.path != path]

    def set_enabled(self, path: str, enabled: bool) -> None:
        for lib in self.libraries:
            if lib.path == path:
                lib.enabled = enabled


def config_dir() -> Path:
    """The per-platform directory for our settings and index cache."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME.lower()


def config_path() -> Path:
    return config_dir() / "config.json"


def load() -> Config:
    """Load the config, falling back to defaults if it is missing or corrupt."""
    path = config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Config()
    except (OSError, json.JSONDecodeError):
        logger.warning("Ignoring unreadable config at %s", path, exc_info=True)
        return Config()

    libraries = [
        Library(path=str(item.get("path", "")), enabled=bool(item.get("enabled", True)))
        for item in raw.get("libraries", [])
        if item.get("path")
    ]
    return Config(
        libraries=libraries,
        case_sensitive=bool(raw.get("case_sensitive", False)),
        last_move_dir=str(raw.get("last_move_dir", "")),
    )


def save(config: Config) -> None:
    """Write the config out, replacing it atomically."""
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        # Losing preferences is an annoyance, not a reason to stop the app.
        logger.warning("Could not save config to %s", path, exc_info=True)
