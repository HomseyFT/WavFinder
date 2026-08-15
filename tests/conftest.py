"""Synthesised WAV files covering the shapes a real sound library contains."""

import struct
from pathlib import Path

import pytest

WAVE_FORMAT_PCM = 0x0001
WAVE_FORMAT_IEEE_FLOAT = 0x0003
WAVE_FORMAT_EXTENSIBLE = 0xFFFE

# GUID suffix shared by every KSDATAFORMAT_SUBTYPE_* value.
_GUID_TAIL = b"\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"


def _chunk(chunk_id: bytes, payload: bytes) -> bytes:
    """A RIFF chunk, word-aligned with a pad byte if the payload is odd."""
    data = chunk_id + struct.pack("<I", len(payload)) + payload
    return data + (b"\x00" if len(payload) & 1 else b"")


def _fmt_payload(fmt_tag: int, channels: int, rate: int, bits: int) -> bytes:
    block_align = channels * bits // 8
    payload = struct.pack(
        "<HHIIHH", fmt_tag, channels, rate, rate * block_align, block_align, bits
    )
    if fmt_tag == WAVE_FORMAT_EXTENSIBLE:
        # cbSize, wValidBitsPerSample, dwChannelMask, SubFormat GUID.
        payload += struct.pack("<HHI", 22, bits, 0x3)
        payload += struct.pack("<H", WAVE_FORMAT_PCM) + _GUID_TAIL
    return payload


def make_wav(
    path: Path,
    *,
    fmt_tag: int = WAVE_FORMAT_PCM,
    channels: int = 2,
    rate: int = 48000,
    bits: int = 16,
    frames: int = 48000,
    bext_description: str = "",
    bext_originator: str = "",
    bext_date: str = "",
    info: dict[str, str] | None = None,
    ixml: str = "",
    junk: int = 0,
    trailing: list[tuple[bytes, bytes]] | None = None,
) -> Path:
    """Write a valid WAV whose header says what the arguments say."""
    block_align = channels * bits // 8
    body = b""
    if junk:
        body += _chunk(b"JUNK", b"\x00" * junk)
    body += _chunk(b"fmt ", _fmt_payload(fmt_tag, channels, rate, bits))

    if bext_description or bext_originator or bext_date:
        bext = bytearray(602)
        for text, offset, length in (
            (bext_description, 0, 256),
            (bext_originator, 256, 32),
            (bext_date, 320, 10),
        ):
            encoded = text.encode("utf-8")[:length]
            bext[offset : offset + len(encoded)] = encoded
        body += _chunk(b"bext", bytes(bext))

    if info:
        info_payload = b"INFO"
        for code, value in info.items():
            raw = value.encode("utf-8") + b"\x00"
            info_payload += code.encode("ascii") + struct.pack("<I", len(raw)) + raw
            if len(raw) & 1:
                info_payload += b"\x00"
        body += _chunk(b"LIST", info_payload)

    if ixml:
        body += _chunk(b"iXML", ixml.encode("utf-8"))

    body += _chunk(b"data", b"\x00" * (frames * block_align))

    # Chunks that sit *after* the audio, the way real libraries write them.
    for chunk_id, payload in trailing or []:
        body += _chunk(chunk_id, payload)

    riff = b"WAVE" + body
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(riff)) + riff)
    return path


def make_sound_ideas_wav(path: Path, description: str, **kwargs) -> Path:
    """A file shaped like the Sound Ideas / Warner Bros. library files.

    Modelled on a real one: a JUNK pad, bext holding the description plus a
    per-library copyright stamp, then a Soundminer SMED blob and an iXML copy of
    the description sitting after the audio.
    """
    return make_wav(
        path,
        junk=52,
        bits=24,
        bext_description=description,
        bext_originator="©Sound IdeasAll Rights Reserved",
        bext_date="2007-03-16",
        trailing=[
            (b"SMED", b"\x00" * 71244),
            (
                b"iXML",
                (
                    '<?xml version="1.0" encoding="UTF-8"?> <BWFXML>'
                    "<IXML_VERSION>1.61</IXML_VERSION><BEXT>"
                    f"<BWF_Description>{description}</BWF_Description>"
                    "</BEXT></BWFXML>"
                ).encode("utf-8"),
            ),
        ],
        **kwargs,
    )


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """A small library exercising the formats and quirks we care about."""
    root = tmp_path / "library"

    make_wav(
        root / "Vehicles" / "car_door_slam.wav",
        bext_description="Car door slam, heavy, exterior",
        frames=96000,  # 2.0s
    )
    make_wav(
        root / "Vehicles" / "CAR_HORN.WAV",  # uppercase extension
        info={"INAM": "Car horn", "IGNR": "Vehicles"},
        frames=24000,  # 0.5s
    )
    make_wav(
        root / "Ambience" / "forest_birds.wav",
        fmt_tag=WAVE_FORMAT_IEEE_FLOAT,
        bits=32,
        frames=480000,  # 10.0s -- would be dropped entirely by stdlib wave
        ixml="<BWFXML><USER><CATEGORY>Ambience</CATEGORY>"
        "<DESCRIPTION>Forest birdsong, morning</DESCRIPTION></USER></BWFXML>",
    )
    make_wav(
        root / "Ambience" / "rain_heavy.wav",
        fmt_tag=WAVE_FORMAT_EXTENSIBLE,
        bits=24,
        frames=144000,  # 3.0s
        bext_description="Heavy rain on a tin roof",
    )
    # Noise a real library is full of, none of which should reach the index.
    (root / "Vehicles" / "._car_door_slam.wav").write_bytes(b"\x00\x05\x16\x07")
    (root / "Ambience" / "notes.txt").write_text("not audio")
    (root / "Ambience" / "truncated.wav").write_bytes(b"RIFF\x10\x00\x00\x00WAVEfmt ")

    return root
