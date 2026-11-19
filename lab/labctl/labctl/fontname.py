"""What a web font file says about itself: its `name` table, read without any font package.

A font states its own licence: the `name` table's record 13 (licence description), 14 (licence
address) and 0 (copyright), with 1 (family) and 5 (version). This module reads them from

- TrueType and OpenType files (`.ttf`, `.otf`): the table directory, then the `name` table;
- WOFF 1 (`.woff`): the same tables, each compressed with zlib (the standard library);
- WOFF 2 (`.woff2`): one Brotli stream holding every table. Brotli is not in the standard
  library: when the `brotli` module happens to be installed it is used, otherwise the file
  cannot be read here (`Names.why` says so) and a caller falls back to what it knows about the
  family (`vendor.font_licence`).

EOT files and font collections are not read (`why` says so). Nothing here downloads or installs
anything.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

COPYRIGHT, FAMILY, VERSION, LICENCE, LICENCE_URL = 0, 1, 5, 13, 14
WANTED = (COPYRIGHT, FAMILY, VERSION, LICENCE, LICENCE_URL)

# WOFF 2's known table tags, by index (the specification's table).
WOFF2_TAGS = (
    "cmap head hhea hmtx maxp name OS/2 post cvt  fpgm glyf loca prep CFF  VORG EBDT EBLC gasp "
    "hdmx kern LTSH PCLT VDMX vhea vmtx BASE GDEF GPOS GSUB EBSC JSTF MATH CBDT CBLC COLR CPAL "
    "SVG  sbix acnt avar bdat bloc bsln cvar fdsc feat fmtx fvar gvar hsty just lcar mort morx "
    "opbd prop trak Zapf Silf Glat Gloc Feat Sill"
)
_TAGS = [WOFF2_TAGS[i * 5 : i * 5 + 4] for i in range((len(WOFF2_TAGS) + 1) // 5)]


@dataclass
class Names:
    """The records of a font's `name` table this module reads (empty when none could be read)."""

    format: str = "unknown"
    records: dict[int, str] = field(default_factory=dict)
    why: str | None = None  # why nothing could be read

    @property
    def family(self) -> str | None:
        return self.records.get(FAMILY)

    @property
    def copyright(self) -> str | None:
        return self.records.get(COPYRIGHT)

    @property
    def licence(self) -> str | None:
        return self.records.get(LICENCE)

    @property
    def licence_url(self) -> str | None:
        return self.records.get(LICENCE_URL)


def kind(body: bytes) -> str:
    head = body[:4]
    if head == b"wOFF":
        return "woff"
    if head == b"wOF2":
        return "woff2"
    if head in (b"\x00\x01\x00\x00", b"true", b"typ1"):
        return "truetype"
    if head == b"OTTO":
        return "opentype"
    if head == b"ttcf":
        return "collection"
    if len(body) > 36 and body[34:36] == b"LP":
        return "embedded-opentype"
    return "unknown"


def _name_table(data: bytes) -> dict[int, str]:
    """The wanted records of a `name` table; Windows Unicode records first, then Mac Roman."""
    if len(data) < 6:
        return {}
    _fmt, count, start = struct.unpack(">HHH", data[:6])
    found: dict[int, tuple[int, str]] = {}
    for i in range(count):
        at = 6 + i * 12
        if at + 12 > len(data):
            break
        platform, encoding, language, name_id, length, offset = struct.unpack(
            ">HHHHHH", data[at : at + 12]
        )
        if name_id not in WANTED:
            continue
        raw = data[start + offset : start + offset + length]
        if platform in (0, 3):
            text = raw.decode("utf-16-be", "replace")
            rank = 0 if (platform, language) in ((3, 0x409), (0, 0)) else 1
        elif platform == 1 and encoding == 0:
            text = raw.decode("mac_roman", "replace")
            rank = 2
        else:
            continue
        text = text.replace("\x00", "").strip()
        if text and (name_id not in found or rank < found[name_id][0]):
            found[name_id] = (rank, text)
    return {k: v for k, (_, v) in found.items()}


def _sfnt(body: bytes) -> dict[int, str]:
    if len(body) < 12:
        return {}
    (num,) = struct.unpack(">H", body[4:6])
    for i in range(num):
        at = 12 + i * 16
        if at + 16 > len(body):
            break
        tag = body[at : at + 4]
        _check, offset, length = struct.unpack(">III", body[at + 4 : at + 16])
        if tag == b"name":
            return _name_table(body[offset : offset + length])
    return {}


def _woff(body: bytes) -> dict[int, str]:
    if len(body) < 44:
        return {}
    (num,) = struct.unpack(">H", body[12:14])
    for i in range(num):
        at = 44 + i * 20
        if at + 20 > len(body):
            break
        tag = body[at : at + 4]
        offset, comp, orig, _check = struct.unpack(">IIII", body[at + 4 : at + 20])
        if tag != b"name":
            continue
        data = body[offset : offset + comp]
        if comp < orig:
            data = zlib.decompress(data)
        return _name_table(data)
    return {}


def _base128(body: bytes, at: int) -> tuple[int, int]:
    value = 0
    for i in range(5):
        byte = body[at + i]
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, at + i + 1
    raise ValueError("bad UIntBase128")


def _woff2(body: bytes) -> tuple[dict[int, str], str | None]:
    try:
        import brotli  # type: ignore[import-not-found]  # optional: not a dependency
    except ImportError:
        return {}, ("a WOFF 2 file is Brotli-compressed and no Brotli module is installed on the "
                    "machine that made the brief")  # fmt: skip
    if body[4:8] == b"ttcf":
        return {}, "a font collection: not read"
    (num,) = struct.unpack(">H", body[12:14])
    (compressed,) = struct.unpack(">I", body[20:24])
    at, offset, name_at = 48, 0, None
    for _ in range(num):
        flags = body[at]
        at += 1
        index = flags & 0x3F
        if index == 63:
            tag = body[at : at + 4].decode("latin-1")
            at += 4
        else:
            tag = _TAGS[index]
        transform = flags >> 6
        orig, at = _base128(body, at)
        length = orig
        transformed = (transform == 0) if tag in ("glyf", "loca") else (transform != 0)
        if transformed:
            length, at = _base128(body, at)
        if tag == "name":
            name_at = (offset, orig)
        offset += length
    if name_at is None:
        return {}, None
    stream = brotli.decompress(body[at : at + compressed])
    start, length = name_at
    return _name_table(stream[start : start + length]), None


def read(body: bytes) -> Names:
    """The `name` records of a font file (`Names.why` says why none, when none)."""
    k = kind(body)
    try:
        if k in ("truetype", "opentype"):
            records = _sfnt(body)
            why = None
        elif k == "woff":
            records, why = _woff(body), None
        elif k == "woff2":
            records, why = _woff2(body)
        elif k == "embedded-opentype":
            return Names(k, {}, "an EOT file: not read")
        elif k == "collection":
            return Names(k, {}, "a font collection: not read")
        else:
            return Names(k, {}, "not a font file this module knows")
    except (struct.error, zlib.error, ValueError, IndexError) as exc:
        return Names(k, {}, f"the file could not be read ({type(exc).__name__})")
    except Exception as exc:  # noqa: BLE001 - a Brotli error: the file cannot be read
        return Names(k, {}, f"the file could not be read ({type(exc).__name__})")
    if not records and why is None:
        why = "the file has no readable name table"
    return Names(k, records, why)
