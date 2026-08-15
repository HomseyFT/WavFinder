# WavFinder

A fuzzy finder for large `.wav` sound-effects libraries. Point it at your
library folders, wait for the first scan, then search by anything the files
say about themselves — filename, folder, or embedded description.

## Running it

```bash
pip install -e ".[dev]"
wavfinder                    # opens with whatever libraries you saved last time
wavfinder /Volumes/SFX       # also adds this folder as a library
```

On Linux, tkinter comes from your package manager rather than pip
(`sudo dnf install python3-tkinter`, or `apt install python3-tk`).

## Using it

**Libraries.** Pick `Add library…` from the Library dropdown to choose a folder.
Add as many as you like; they are all searched together. `Manage libraries…`
lets you remove one or untick it to leave it out of the search without
forgetting it. Selecting a single library from the dropdown narrows the results
to just that one.

**Searching.** Type in the search box. Every word you type has to match
something, so `car door` finds car doors rather than everything car-ish. Short
words (three letters or fewer) must match exactly; longer words tolerate a typo.
A word also matches anything starting with it, so `door` finds `doorway`.

**Match case** switches between case-insensitive (the default) and exact case.

Search terms are highlighted in the File Details pane when you select a result.

**Moving files.** Select a file, then `Move To…` or `Copy To…`. If a file of
that name is already there you get the choice to keep both, replace, or cancel.
Sidecar files (macOS `._` stubs, and `.reapeaks` / `.pkf` / `.sfk` / `.asd` /
`.xmp` peak and metadata files) travel with the audio. A moved file stays in the
index at its new location, so you can still find it.

## What it reads

Technical fields come from the RIFF header, so PCM, 32-bit float and
`WAVE_FORMAT_EXTENSIBLE` files all work. Descriptions are pulled from whichever
of these a file carries:

| Source | Typical writer |
| --- | --- |
| `bext` chunk | Broadcast Wave / field recorders |
| `iXML` chunk | Soundminer, UCS-tagged libraries |
| `LIST INFO` chunk | older commercial libraries |
| embedded `id3 ` chunk | some sample packs |

Where a file repeats the same description in several of those (Sound Ideas
libraries write it into all three), the first one wins and the copies are
dropped, so the details pane says it once.

Publisher fields — copyright line, web address, origination date — are read but
deliberately kept out of the search. A commercial library stamps every file with
the identical line, so leaving it searchable would make "sound" match all
200,000 files.

Only chunk headers and the descriptive chunks are read, never the audio and
never proprietary blocks such as Soundminer's `SMED`. Reading a 7.7 MB library
file costs about 27 KB.

## The index cache

Results are cached in a small SQLite file so relaunching does not re-read the
whole library. A file is re-read only when its size or modification time
changes. Cached entries for files you have deleted are dropped on the next full
scan of that library.

Settings and cache live in:

- macOS — `~/Library/Application Support/WavFinder/`
- Linux — `~/.config/wavfinder/`
- Windows — `%APPDATA%\WavFinder\`

Deleting that folder resets everything, costing only a rescan.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The UI tests drive the real widgets and skip themselves where there is no
display. Test WAV files are synthesised in `tests/conftest.py`, so no binary
fixtures are checked in.

## Building

```bash
pyinstaller wavfinder.spec
```

Tagging `v*` builds Windows, macOS and Linux artifacts through GitHub Actions.

**macOS note:** the `.app` is not code-signed or notarized, so Gatekeeper will
refuse to open it normally. Signing requires a paid Apple Developer account.
Until then, run from source, or clear the quarantine flag after downloading:

```bash
xattr -dr com.apple.quarantine /Applications/WavFinder.app
```
