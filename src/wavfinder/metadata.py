"""Read technical and descriptive metadata out of a .wav file.

This parses the RIFF container directly rather than going through the stdlib
``wave`` module. ``wave`` only accepts WAVE_FORMAT_PCM, so it raises on the
32-bit float and WAVE_FORMAT_EXTENSIBLE files that are common in professional
sound-effects libraries -- those files would otherwise be dropped from the
index without the user ever knowing they were missing.

Sound libraries also keep their descriptions in places ``wave`` cannot see: the
Broadcast Wave ``bext`` chunk, an ``iXML`` block, a RIFF ``LIST INFO`` chunk, or
an embedded ID3 tag. We walk the chunk list once and collect whichever of those
are present.
"""

import logging
import struct
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path

from wavfinder.models import WavMetadata

logger = logging.getLogger(__name__)

WAVE_FORMAT_PCM = 0x0001
WAVE_FORMAT_IEEE_FLOAT = 0x0003
WAVE_FORMAT_EXTENSIBLE = 0xFFFE

# Never pull a whole chunk into memory if it is larger than this. Descriptive
# chunks are a few KB at most; anything bigger is either corrupt or audio.
MAX_CHUNK_BYTES = 1 << 20  # 1 MiB

# The only chunks whose contents we read. Everything else is seeked past.
READABLE_CHUNKS = frozenset(
    {b"fmt ", b"ds64", b"bext", b"LIST", b"iXML", b"ixml", b"IXML", b"id3 ", b"ID3 "}
)

# RIFF LIST INFO four-character codes -> readable names.
RIFF_INFO_NAMES = {
    "INAM": "Title",
    "ICMT": "Comment",
    "IART": "Artist",
    "IPRD": "Album",
    "IGNR": "Category",
    "ISBJ": "Subject",
    "IKEY": "Keywords",
    "ICRD": "Date",
    "IENG": "Engineer",
    "ISFT": "Software",
    "ICOP": "Copyright",
    "ITCH": "Technician",
    "ISRC": "Source",
    "IARL": "Archival Location",
}

# ID3 frame ids -> readable names, for WAVs carrying an embedded ID3 chunk.
ID3_FRAME_NAMES = {
    "TIT2": "Title",
    "TALB": "Album",
    "TPE1": "Artist",
    "TCON": "Category",
    "COMM": "Comment",
    "TXXX": "Keywords",
}

# iXML elements worth surfacing. iXML documents can carry a hundred fields;
# these are the ones that describe the sound rather than the session.
IXML_NAMES = {
    "DESCRIPTION": "Description",
    # Sound Ideas / Warner Bros. libraries mirror the bext description here.
    "BWF_DESCRIPTION": "Description",
    "BWF_ORIGINATION_DATE": "Date",
    "TRACK_TITLE": "Title",
    "PROJECT": "Project",
    "CATEGORY": "Category",
    "SUBCATEGORY": "Subcategory",
    "CATID": "Category ID",
    "KEYWORDS": "Keywords",
    "NOTES": "Comment",
    "MICROPHONE": "Microphone",
    "LIBRARY": "Library",
    "SCENE": "Scene",
    "DESIGNER": "Designer",
}


def extract_metadata(path: Path) -> WavMetadata | None:
    """Extract metadata from a .wav file. Returns None if it cannot be read."""
    try:
        with open(path, "rb") as fh:
            parsed = _parse_riff(fh)
    except OSError:
        logger.warning("Could not open %s", path)
        return None
    except Exception:
        logger.warning("Malformed WAV %s", path, exc_info=True)
        return None

    if parsed is None:
        logger.debug("Not a RIFF/WAVE file: %s", path)
        return None

    fmt = parsed["fmt"]
    if fmt is None:
        logger.debug("No fmt chunk in %s", path)
        return None

    sample_rate = fmt["sample_rate"]
    bytes_per_frame = fmt["block_align"] or (fmt["channels"] * fmt["bit_depth"] // 8)
    if sample_rate > 0 and bytes_per_frame > 0:
        duration = parsed["data_size"] / (sample_rate * bytes_per_frame)
    else:
        duration = 0.0

    return WavMetadata(
        file_path=path.resolve(),
        file_name=path.name,
        duration_seconds=round(duration, 3),
        sample_rate=sample_rate,
        channels=fmt["channels"],
        bit_depth=fmt["bit_depth"],
        tags=parsed["tags"],
    )


# --------------------------------------------------------------- RIFF walk --
def _parse_riff(fh) -> dict | None:
    """Walk the chunk list once, collecting format and descriptive metadata.

    Returns None if the file is not a RIFF/WAVE container at all.
    """
    header = fh.read(12)
    if len(header) < 12:
        return None
    magic, form = header[:4], header[8:12]
    if magic not in (b"RIFF", b"RF64") or form != b"WAVE":
        return None

    result: dict = {"fmt": None, "data_size": 0, "tags": {}}
    # RF64 stores the real (>4 GiB) data size in a ds64 chunk, because the
    # 32-bit size field in the data chunk header is pinned at 0xFFFFFFFF.
    ds64_data_size: int | None = None

    while True:
        chunk_header = fh.read(8)
        if len(chunk_header) < 8:
            break
        chunk_id = chunk_header[:4]
        (chunk_size,) = struct.unpack("<I", chunk_header[4:8])
        # Chunks are word-aligned: an odd-sized chunk is followed by a pad byte.
        advance = chunk_size + (chunk_size & 1)

        if chunk_id == b"data":
            # RF64 pins this field at 0xFFFFFFFF; the ds64 chunk (which always
            # precedes data) carries the true size. Use it so we skip exactly
            # the audio and can still read chunks that follow it.
            if chunk_size == 0xFFFFFFFF and ds64_data_size is not None:
                result["data_size"] = ds64_data_size
                advance = ds64_data_size + (ds64_data_size & 1)
            else:
                result["data_size"] = chunk_size
            fh.seek(advance, 1)
            continue

        # Only pull in chunks we actually parse. Real libraries carry large
        # proprietary blocks -- Soundminer's SMED chunk is ~70 KB per file --
        # and reading those just to discard them would add tens of gigabytes of
        # pointless disk traffic across a full library scan.
        if chunk_id not in READABLE_CHUNKS or chunk_size > MAX_CHUNK_BYTES:
            fh.seek(advance, 1)
            continue

        payload = fh.read(chunk_size)
        if len(payload) < chunk_size:  # truncated file
            break
        if chunk_size & 1:
            fh.seek(1, 1)

        try:
            if chunk_id == b"fmt ":
                result["fmt"] = _parse_fmt(payload)
            elif chunk_id == b"ds64":
                ds64_data_size = _parse_ds64(payload)
            else:
                if chunk_id == b"bext":
                    found = _parse_bext(payload)
                elif chunk_id == b"LIST":
                    found = _parse_list(payload)
                elif chunk_id.lower() == b"ixml":
                    found = _parse_ixml(payload)
                else:
                    found = _parse_id3(payload)
                # First chunk to supply a field wins. These libraries repeat the
                # same description in bext, iXML and ID3; bext comes first and is
                # the most reliable, so a later copy must not overwrite it.
                for key, value in found.items():
                    result["tags"].setdefault(key, value)
        except Exception:
            # A bad optional chunk should never cost us the whole file.
            logger.debug("Skipping unreadable %r chunk", chunk_id, exc_info=True)

    return result


def _parse_fmt(payload: bytes) -> dict | None:
    if len(payload) < 16:
        return None
    fmt_tag, channels, sample_rate, _byte_rate, block_align, bit_depth = struct.unpack(
        "<HHIIHH", payload[:16]
    )
    # For WAVE_FORMAT_EXTENSIBLE the real format sits in the SubFormat GUID and
    # the meaningful bit count in the extension's wValidBitsPerSample.
    if fmt_tag == WAVE_FORMAT_EXTENSIBLE and len(payload) >= 40:
        (valid_bits,) = struct.unpack("<H", payload[18:20])
        (sub_tag,) = struct.unpack("<H", payload[24:26])
        if valid_bits:
            bit_depth = valid_bits
        fmt_tag = sub_tag
    return {
        "format": fmt_tag,
        "channels": channels,
        "sample_rate": sample_rate,
        "block_align": block_align,
        "bit_depth": bit_depth,
    }


def _parse_ds64(payload: bytes) -> int | None:
    if len(payload) < 16:
        return None
    (_riff_size, data_size) = struct.unpack("<QQ", payload[:16])
    return data_size


def _parse_bext(payload: bytes) -> dict[str, str]:
    """Broadcast Wave extension. Description is the first 256 bytes."""
    tags: dict[str, str] = {}
    fields = (
        ("Description", 0, 256),
        ("Originator", 256, 32),
        ("Originator Reference", 288, 32),
        ("Date", 320, 10),
    )
    for name, offset, length in fields:
        value = _decode(payload[offset : offset + length])
        if value:
            tags[name] = value
    if len(payload) > 602:
        history = _decode(payload[602:])
        if history:
            tags["Coding History"] = history
    return tags


def _parse_list(payload: bytes) -> dict[str, str]:
    """A LIST chunk of type INFO holds classic RIFF descriptive tags."""
    tags: dict[str, str] = {}
    if len(payload) < 4 or payload[:4] != b"INFO":
        return tags
    pos = 4
    while pos + 8 <= len(payload):
        code = payload[pos : pos + 4].decode("ascii", "replace")
        (size,) = struct.unpack("<I", payload[pos + 4 : pos + 8])
        pos += 8
        value = _decode(payload[pos : pos + size])
        if value:
            tags[RIFF_INFO_NAMES.get(code, code)] = value
        pos += size + (size & 1)
    return tags


def _parse_ixml(payload: bytes) -> dict[str, str]:
    """iXML is an XML document embedded in its own chunk."""
    tags: dict[str, str] = {}
    text = payload.split(b"\x00", 1)[0]
    root = ET.fromstring(text.decode("utf-8", "replace"))
    for element in root.iter():
        name = IXML_NAMES.get(element.tag.upper())
        if name and element.text and element.text.strip():
            tags.setdefault(name, element.text.strip())
    return tags


def _parse_id3(payload: bytes) -> dict[str, str]:
    """Some WAVs carry a full ID3 tag in an 'id3 ' chunk."""
    from mutagen.id3 import ID3

    tags: dict[str, str] = {}
    frames = ID3(BytesIO(payload))
    for frame_id, name in ID3_FRAME_NAMES.items():
        for frame in frames.getall(frame_id):
            value = str(frame).strip()
            if value:
                tags.setdefault(name, value)
                break
    return tags


def _decode(raw: bytes) -> str:
    """Decode a null-padded RIFF string field."""
    return raw.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
