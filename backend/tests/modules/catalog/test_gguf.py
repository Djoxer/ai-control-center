"""GGUF header reader: every value type, the cut-off buffer at every byte, broken files."""
import struct

import pytest

from control_center.modules.catalog import gguf
from control_center.modules.catalog.gguf import GgufError, NeedMore, parse_header, partial_header, write_header

KV = {
    "general.architecture": "qwen3moe",
    "general.file_type": 15,
    "qwen3moe.block_count": 48,
    "qwen3moe.attention.head_count_kv": [0, 4, 0, 4],      # per layer: kept
    "qwen3moe.rope.freq_base": 10000000.0,
    "qwen3moe.rope.mrope_interleaved": True,
    "qwen3moe.bias": -3,                                   # negative -> i64
    "tokenizer.ggml.tokens": ["a", "bb", "ccc"],           # strings: walked through, not kept
    "tokenizer.ggml.scores": [0.5] * 600,                  # longer than KEEP_LIST: not kept
    "general.name": "Qwen3 Coder",
}


def _raw_kv(key: str, kind: int, payload: bytes) -> bytes:
    k = key.encode()
    return struct.pack("<Q", len(k)) + k + struct.pack("<I", kind) + payload


def _file(*entries: bytes, version: int = 3) -> bytes:
    return b"GGUF" + struct.pack("<IQQ", version, 7, len(entries)) + b"".join(entries)


def test_round_trip_keeps_scalars_and_short_number_lists():
    h = parse_header(write_header(KV, tensor_count=579))
    assert (h.version, h.tensor_count, h.kv_count, h.complete) == (3, 579, len(KV), True)
    assert h.kv == {
        "general.architecture": "qwen3moe", "general.file_type": 15, "qwen3moe.block_count": 48,
        "qwen3moe.attention.head_count_kv": [0, 4, 0, 4], "qwen3moe.rope.freq_base": 10000000.0,
        "qwen3moe.rope.mrope_interleaved": True, "qwen3moe.bias": -3, "general.name": "Qwen3 Coder"}
    assert h.end == len(write_header(KV, tensor_count=579))


def test_bytes_after_the_header_do_not_matter():
    data = write_header(KV)
    assert parse_header(data + b"\x00" * 1000).end == len(data)


@pytest.mark.parametrize(("kind", "payload", "value"), [
    (0, struct.pack("<B", 200), 200), (1, struct.pack("<b", -5), -5), (2, struct.pack("<H", 60000), 60000),
    (3, struct.pack("<h", -300), -300), (4, struct.pack("<I", 2 ** 31), 2 ** 31), (5, struct.pack("<i", -7), -7),
    (6, struct.pack("<f", 0.25), 0.25), (7, struct.pack("<?", False), False), (10, struct.pack("<Q", 2 ** 40), 2 ** 40),
    (11, struct.pack("<q", -(2 ** 40)), -(2 ** 40)), (12, struct.pack("<d", 1e-6), 1e-6),
])
def test_every_scalar_type(kind, payload, value):
    assert parse_header(_file(_raw_kv("x.v", kind, payload))).kv == {"x.v": value}


def test_number_lists_of_every_width_and_nested_lists():
    u16 = struct.pack("<IQ", 2, 3) + struct.pack("<3H", 1, 2, 3)
    f64 = struct.pack("<IQ", 12, 2) + struct.pack("<2d", 0.5, 1.5)
    nested = struct.pack("<IQ", 9, 2) + struct.pack("<IQ", 4, 1) + struct.pack("<I", 9) + struct.pack("<IQ", 8, 1) \
        + struct.pack("<Q", 2) + b"hi"
    h = parse_header(_file(_raw_kv("a", 9, u16), _raw_kv("b", 9, f64), _raw_kv("c", 9, nested),
                           _raw_kv("d", 4, struct.pack("<I", 1))))
    assert h.kv == {"a": [1, 2, 3], "b": [0.5, 1.5], "d": 1}       # nested list skipped, next entry still read


def test_a_list_exactly_at_the_limit_is_kept():
    data = write_header({"n": list(range(gguf.KEEP_LIST)), "m": list(range(gguf.KEEP_LIST + 1))})
    assert list(parse_header(data).kv) == ["n"]


def test_every_cut_asks_for_more_and_keeps_what_was_complete():
    data = write_header(KV)
    kept = list(parse_header(data).kv)
    counts = []
    for cut in range(len(data)):
        with pytest.raises(NeedMore) as info:
            parse_header(data[:cut])
        assert cut < info.value.at <= len(data)
        keys = list(info.value.kv)
        assert keys == kept[:len(keys)]                                 # complete entries only, in file order
        counts.append(len(keys))
    assert counts == sorted(counts) and counts[-1] == len(kept) - 1    # the last entry needs the last byte


def test_partial_header_at_the_size_limit():
    data = write_header(KV, tensor_count=12)
    cut = data[: len(data) - 30]
    with pytest.raises(NeedMore) as info:
        parse_header(cut)
    h = partial_header(cut, info.value)
    assert (h.version, h.tensor_count, h.kv_count, h.complete, h.end) == (3, 12, len(KV), False, len(cut))
    assert h.kv["general.architecture"] == "qwen3moe" and "general.name" not in h.kv
    assert partial_header(b"GGUF", NeedMore(24, {})).kv_count == 0     # cut before the counts


@pytest.mark.parametrize(("data", "message"), [
    (b"PK\x03\x04" + b"\x00" * 30, "Keine GGUF-Datei"),
    (b"GGUF" + struct.pack("<IQQ", 1, 0, 0), "Version 1"),
    (b"GGUF" + struct.pack(">IQQ", 3, 0, 0), "Big-Endian"),
    (b"GGUF" + struct.pack("<IQQ", 7, 0, 0), "Unbekannte GGUF-Version 7"),
    (b"GGUF" + struct.pack("<IQQ", 3, 0, gguf.MAX_KEYS + 1), "Metadaten-Einträge"),
    (_file(_raw_kv("x", 13, b"")), "Unbekannter Werttyp 13"),
    (_file(_raw_kv("x", 9, struct.pack("<IQ", 13, 1))), "Unbekannter Listentyp 13"),
    (_file(_raw_kv("x", 8, struct.pack("<Q", gguf.MAX_STRING + 1))), "Zeichenkette"),
    (_file(_raw_kv("x", 9, struct.pack("<IQ", 4, gguf.MAX_ARRAY_BYTES // 4 + 1))), "Liste mit"),
    (_file(_raw_kv("x", 9, struct.pack("<IQ", 8, gguf.MAX_STRINGS + 1))), "Liste mit"),
])
def test_broken_files_are_errors_not_requests_for_more(data, message):
    with pytest.raises(GgufError, match=message):
        parse_header(data)


def test_version_2_is_read_like_3():
    assert parse_header(write_header({"a": 1}, version=2)).kv == {"a": 1}


def test_a_broken_utf8_string_does_not_stop_the_reader():
    h = parse_header(_file(_raw_kv("name", 8, struct.pack("<Q", 2) + b"\xff\xfe")))
    assert h.kv["name"] == "��"
