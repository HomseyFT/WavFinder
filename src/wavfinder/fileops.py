"""Move or copy a wav out of a library, taking its sidecar files with it."""

import shutil
from dataclasses import dataclass, field
from pathlib import Path

# Extensions written *alongside* a wav by editors and librarians. These share
# the audio file's stem and are useless on their own, so a move should carry
# them. Anything not on this list is left alone -- a stem match is not enough
# reason to move a stranger's file.
SIDECAR_SUFFIXES = (
    ".reapeaks",  # Reaper peak cache
    ".pkf",  # Pro Tools peak file
    ".sfk",  # Sound Forge peak file
    ".asd",  # Ableton analysis file
    ".xmp",  # Adobe sidecar metadata
)


class CollisionError(Exception):
    """Raised when the destination already holds a file of that name."""

    def __init__(self, destination: Path) -> None:
        super().__init__(f"{destination.name} already exists in {destination.parent}")
        self.destination = destination


@dataclass
class TransferResult:
    source: Path
    destination: Path
    copied: bool
    sidecars: list[Path] = field(default_factory=list)


def find_sidecars(source: Path) -> list[Path]:
    """Return the sidecar files that belong to *source*."""
    found: list[Path] = []
    apple_double = source.with_name("._" + source.name)
    if apple_double.is_file():
        found.append(apple_double)
    for suffix in SIDECAR_SUFFIXES:
        # Both conventions are in the wild: "clip.wav.pkf" and "clip.pkf".
        for candidate in (
            source.with_name(source.name + suffix),
            source.with_suffix(suffix),
        ):
            if candidate.is_file() and candidate not in found:
                found.append(candidate)
    return found


def unique_destination(destination: Path) -> Path:
    """Return *destination* with ' (n)' appended until the name is free."""
    if not destination.exists():
        return destination
    stem, suffix = destination.stem, destination.suffix
    counter = 1
    while True:
        candidate = destination.with_name(f"{stem} ({counter}){suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def transfer(
    source: Path,
    dest_dir: Path,
    *,
    copy: bool = False,
    overwrite: bool = False,
    rename_on_collision: bool = False,
    include_sidecars: bool = True,
) -> TransferResult:
    """Move (or copy) *source* into *dest_dir*.

    Raises CollisionError when the name is taken and neither *overwrite* nor
    *rename_on_collision* was chosen, so the caller can ask the user first.
    """
    source = Path(source)
    dest_dir = Path(dest_dir)
    if not source.is_file():
        raise FileNotFoundError(source)
    if not dest_dir.is_dir():
        raise NotADirectoryError(dest_dir)

    destination = dest_dir / source.name
    if destination.resolve() == source.resolve():
        raise ValueError("Source and destination are the same file")

    if destination.exists():
        if rename_on_collision:
            destination = unique_destination(destination)
        elif not overwrite:
            raise CollisionError(destination)

    moved_sidecars: list[Path] = []
    for sidecar in find_sidecars(source) if include_sidecars else []:
        # Keep the sidecar's relationship to the (possibly renamed) audio file.
        target = dest_dir / sidecar.name.replace(source.stem, destination.stem, 1)
        try:
            moved_sidecars.append(_place(sidecar, unique_destination(target), copy))
        except OSError:
            # A sidecar is a cache file; losing one must not fail the transfer.
            continue

    _place(source, destination, copy)
    return TransferResult(
        source=source, destination=destination, copied=copy, sidecars=moved_sidecars
    )


def _place(source: Path, destination: Path, copy: bool) -> Path:
    if copy:
        shutil.copy2(source, destination)
    else:
        # shutil.move handles the cross-filesystem case, which is the norm here:
        # people pull sounds off a library drive onto a local project disk.
        shutil.move(str(source), str(destination))
    return destination
