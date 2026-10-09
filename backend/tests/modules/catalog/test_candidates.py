"""Candidate check, registry side: reading the header in growing pieces, what the facts say about a model."""
import asyncio
import json

import pytest

from control_center.adapters.library import FakeLibrary, Layer, LibraryNotFound, parse_manifest, parse_ref
from control_center.modules.catalog.candidates import (
    CandidateFacts, capabilities, fetch_candidate, params_from, read_header, version_ok, version_tuple,
)
from control_center.modules.catalog.gguf import GgufError, write_header
from control_center.modules.catalog.settings import CatalogSettings

KIB = 1024


class StubLibrary:
    """Serves one weights file from memory and records every piece that was asked for."""
    simulated = False

    def __init__(self, data: bytes):
        self.data = data
        self.calls: list[tuple[int, int]] = []

    async def blob(self, ref, digest, start, length, timeout):
        self.calls.append((start, length))
        return self.data[start:start + length]


def _header(tokens: int) -> bytes:
    kv = {"general.architecture": "qwen3", "qwen3.block_count": 40, "qwen3.context_length": 40960}
    kv["tokenizer.ggml.tokens"] = [f"token-{i}" for i in range(tokens)]
    kv["tokenizer.ggml.eos_token_id"] = 2                      # after the big table, like in llama.cpp files
    return write_header(kv)


def _read(lib, size, cap, first=KIB):
    layer = Layer("application/vnd.ollama.image.model", "sha256:" + "0" * 64, size)
    return asyncio.run(read_header(lib, parse_ref("m:1"), layer, cap, 5, first=first))


def test_the_header_is_read_in_doubling_pieces_without_reading_twice():
    data = _header(3000)                                       # ~45 KiB of metadata
    lib = StubLibrary(data + b"\x00" * 100 * KIB)              # the tensors follow
    header, read = _read(lib, len(data) + 100 * KIB, cap=1024 * KIB)
    assert header.complete and header.kv["tokenizer.ggml.eos_token_id"] == 2
    starts = [s for s, _ in lib.calls]
    assert starts[0] == 0 and all(b == a + n for (a, n), b in zip(lib.calls, starts[1:]))   # appended, no gaps
    assert [s + n for s, n in lib.calls][:4] == [KIB, 2 * KIB, 4 * KIB, 8 * KIB]
    assert len(data) <= read < 2 * len(data)                   # at most one doubling too far


def test_at_the_size_limit_the_first_entries_are_kept():
    data = _header(3000)
    header, read = _read(StubLibrary(data), len(data), cap=8 * KIB)
    assert not header.complete and read == 8 * KIB
    assert header.kv == {"general.architecture": "qwen3", "qwen3.block_count": 40, "qwen3.context_length": 40960}


def test_a_file_that_ends_inside_the_header_is_an_error():
    data = _header(3000)
    with pytest.raises(GgufError, match="endet mitten in den Metadaten"):
        _read(StubLibrary(data[:5000]), 10 ** 9, cap=1024 * KIB)            # storage gives less than asked
    with pytest.raises(GgufError, match="endet mitten in den Metadaten"):
        _read(StubLibrary(data[:5000]), 5000, cap=1024 * KIB)               # the manifest's size is reached


def test_a_small_file_is_read_in_one_piece():
    data = _header(10)
    lib = StubLibrary(data)
    header, read = _read(lib, len(data), cap=1024 * KIB, first=1024 * KIB)
    assert header.complete and lib.calls == [(0, 1024 * KIB)] and read == len(data)   # the storage says where it ends


def test_the_manifest_size_is_no_limit_for_the_header():
    """Sizes in a manifest can be wrong (09.10.: config of qwen3-coder:30b declared 539, delivered 542)."""
    data = _header(3000)
    header, _ = _read(StubLibrary(data), size=100, cap=1024 * KIB)
    assert header.complete and header.kv["tokenizer.ggml.eos_token_id"] == 2


@pytest.mark.parametrize(("data", "out"), [
    ({"num_ctx": 65536, "stop": ["<|im_end|>", "<|endoftext|>"], "temperature": 0.7, "top_k": 20.0,
      "repeat_penalty": 1, "penalize_newline": False, "x": None, "min_p": 1e-05},
     {"num_ctx": ["65536"], "stop": ["<|im_end|>", "<|endoftext|>"], "temperature": ["0.7"], "top_k": ["20"],
      "repeat_penalty": ["1"], "penalize_newline": ["false"], "min_p": ["1e-05"]}),
    ({}, {}),
])
def test_params_look_like_api_show(data, out):
    assert params_from(data) == out


QWEN = {"general.architecture": "qwen3"}


@pytest.mark.parametrize(("kv", "template", "config", "projector", "caps", "note"), [
    (QWEN, "{{ if .Tools }}x{{ end }}{{ .Prompt }}", {}, False, ["completion", "tools"], "Template nutzt .Tools"),
    (QWEN, "{{ .Prompt }}", {"parser": "qwen3-coder"}, False, ["completion", "tools"], "Parser „qwen3-coder“"),
    (QWEN, "{{ range .Messages }}{{ .Content }}{{ end }}", {}, False, ["completion"], "kennt kein .Tools"),
    (QWEN, None, {}, False, ["completion"], "Werkzeuge unklar"),
    (QWEN, "{{ .Prompt }}{{ .Suffix }}", {}, False, ["completion", "insert"], "kennt kein .Tools"),
    (QWEN, "{{ .ToolCalls }}", {}, False, ["completion"], "kennt kein .Tools"),          # not .Tools
    ({"general.architecture": "nomic-bert", "nomic-bert.pooling_type": 1}, None, {}, False, ["embedding"],
     "Einbettungsmodell"),
    ({"general.architecture": "gemma3", "gemma3.vision.block_count": 27}, "x", {}, False, ["completion", "vision"],
     "Encoder in der Gewichtsdatei"),
    (QWEN, "{{ .Prompt }}", {"parser": "qwen3.5"}, True, ["completion", "tools", "vision", "thinking"],
     "eigene Encoder-Datei"),
    (QWEN, "{{ if .Tools }}{{ end }}<think>", {}, False, ["completion", "tools", "thinking"], ".Tools"),
    (QWEN, "{{ .Prompt }}", {"model_family": "gptoss"}, False, ["completion", "thinking"], "kennt kein .Tools"),
    (QWEN, "{{ .Prompt }}", {"parser": "my-thinking-parser"}, False, ["completion", "tools", "thinking"], "Parser"),
    ({}, None, {"remote_host": "https://ollama.com:443", "capabilities": ["completion", "tools"]}, False,
     ["completion", "tools"], None),
])
def test_capabilities_are_derived_like_ollama_does(kv, template, config, projector, caps, note):
    got, notes = capabilities(kv, template, config, projector)
    assert got == caps
    assert (notes == []) if note is None else any(note in n for n in notes)


def test_facts_of_every_sample():
    lib, cfg = FakeLibrary(), CatalogSettings()

    def facts(name):
        return asyncio.run(fetch_candidate(lib, parse_ref(name), cfg, now=1_760_000_000))

    coder = facts("qwen3-coder:30b")
    r = coder.record
    assert (coder.name, coder.host, coder.simulated, coder.error) == ("qwen3-coder:30b", "registry.ollama.ai", True, None)
    assert (r.architecture, r.info("block_count"), r.info("attention.head_count_kv"), r.info("context_length")) == (
        "qwen3moe", 48, 4, 262144)
    assert (r.family, r.parameter_size, r.quantization, r.format) == ("qwen3moe", "30.5B", "Q4_K_M", "gguf")
    assert coder.weights_bytes == 18_556_688_736 and coder.download_bytes > coder.weights_bytes
    assert r.size == coder.download_bytes and coder.projector_bytes == 0 and coder.header_complete
    assert (coder.renderer, coder.parser, r.template_hash) == ("qwen3-coder", "qwen3-coder", None)
    assert "tokenizer.ggml.tokens" not in r.model_info                       # big tables stay out
    assert r.param("temperature") == "0.7" and r.parameters["stop"][1] == "<|im_end|>"

    qwen35 = facts("qwen3.5:9b")
    assert qwen35.record.weights_digest == "dec52a44569a2a25341c4e4d3fee25846eed4f6f0b936278e3a3c900bb99d37c"
    assert qwen35.download_bytes == 6_594_474_711 and qwen35.projector_bytes == 921_704_832   # = installed size
    assert qwen35.requires == "0.17.1" and qwen35.record.capabilities == ["completion", "tools", "vision", "thinking"]
    assert qwen35.record.info("attention.head_count_kv")[3] == 4 and qwen35.record.info("full_attention_interval") == 4

    gemma = facts("gemma3:12b")
    assert gemma.record.capabilities == ["completion", "vision"] and gemma.record.info("attention.sliding_window") == 1024

    gemma4 = facts("gemma4:latest")
    plan = gemma4.record.info("attention.sliding_window_pattern")
    assert plan[:6] == [True] * 5 + [False] and len(plan) == 42 and all(isinstance(v, bool) for v in plan)
    assert gemma4.record.capabilities == ["completion", "tools", "vision"] and gemma4.projector_bytes == 920_000_000

    cloud = facts("qwen3-coder:480b-cloud")
    assert cloud.error == "Cloud-Modell: läuft auf https://ollama.com:443, nicht auf dieser Karte."
    assert cloud.weights_bytes == 0 and cloud.header_bytes == 0

    with pytest.raises(LibraryNotFound):
        facts("nope:1b")


class OneModelLibrary(FakeLibrary):
    """A FakeLibrary whose manifest and config come from the test. config_short: the manifest declares the
    config this many bytes smaller than it is (seen in the real registry)."""

    def __init__(self, config: dict, layers: list[dict], blobs: dict[str, bytes], config_short: int = 0):
        super().__init__()
        self.cfg, self.layers, self.blobs, self.short = config, layers, blobs, config_short

    async def manifest(self, ref, timeout):
        cfg = json.dumps(self.cfg).encode()
        self.blobs["sha256:" + "c" * 64] = cfg
        return parse_manifest(json.dumps({"config": {"digest": "sha256:" + "c" * 64, "size": len(cfg) - self.short},
                                          "layers": self.layers}).encode())

    async def blob(self, ref, digest, start, length, timeout):
        return self.blobs[digest][start:start + length]


def _layer(kind, data, digest):
    return {"mediaType": f"application/vnd.ollama.image.{kind}", "digest": digest, "size": len(data)}


def test_unusual_models_end_up_as_error_or_note():
    cfg = CatalogSettings()
    weights = write_header({"general.architecture": "llama"})
    w1, w2, p = "sha256:" + "1" * 64, "sha256:" + "2" * 64, "sha256:" + "3" * 64

    def run(config, layers, blobs):
        return asyncio.run(fetch_candidate(OneModelLibrary(config, layers, blobs), parse_ref("x:1"), cfg, now=0))

    safetensors = run({"model_format": "safetensors"}, [_layer("model", weights, w1)], {w1: weights})
    assert safetensors.error == "Format „safetensors“ – der Katalog liest nur GGUF."
    two = run({}, [_layer("model", weights, w1), _layer("model", weights, w2)], {w1: weights, w2: weights})
    assert two.error is None and two.weights_bytes == 2 * len(weights) and "2 Gewichtsdateien" in two.notes[0]
    broken = run({}, [_layer("model", b"PK..", w1), _layer("params", b"{oops", p)], {w1: b"PK..", p: b"{oops"})
    assert broken.error.startswith("Keine GGUF-Datei") and broken.notes == [
        "Parameter nicht lesbar (kein JSON, beginnt mit „{oops“) – ohne diese Angaben gerechnet."]
    empty = run({}, [_layer("params", b"{}", p)], {p: b"{}"})
    assert empty.error == "Keine Gewichte im Manifest – nichts, was auf die Karte käme."


def test_facts_survive_json_and_tolerate_other_versions():
    f = asyncio.run(fetch_candidate(FakeLibrary(), parse_ref("qwen3:14b"), CatalogSettings(), now=5.0))
    data = json.loads(json.dumps(f.to_json()))
    back = CandidateFacts.from_json({**data, "added_later": 1})
    assert back == f
    minimal = CandidateFacts.from_json({"name": "a:1", "host": "h", "page": None, "checked_at": 1.0,
                                        "simulated": False, "download_bytes": 1, "weights_bytes": 1})
    assert minimal.record.name == "a:1" and minimal.notes == []


@pytest.mark.parametrize(("installed", "required", "ok"), [
    ("0.12.6", "0.17.1", False), ("0.17.1", "0.17.1", True), ("0.17.10", "0.17.9", True), ("0.18", "0.17.1", True),
    ("0.17", "0.17.0", True), ("0.13.0-rc1", "0.13.0", True), ("v0.12.6", "0.12.7", False), (None, "0.1", None),
    ("0.12.6", None, None), ("dev", "0.1", None),
])
def test_minimum_ollama_version(installed, required, ok):
    assert version_ok(installed, required) is ok


def test_version_tuple():
    assert version_tuple(" 0.13.0-rc1") == (0, 13, 0) and version_tuple("") is None


def test_a_config_bigger_than_declared_is_read_completely():
    """qwen3-coder:30b on 09.10.: manifest 539 bytes, storage 542 - the parser and renderer sat in the last ones."""
    weights, w1 = write_header({"general.architecture": "qwen3moe"}), "sha256:" + "1" * 64
    lib = OneModelLibrary({"model_type": "30.5B", "file_type": "Q4_K_M", "parser": "qwen3-coder"},
                          [_layer("model", weights, w1)], {w1: weights}, config_short=3)
    f = asyncio.run(fetch_candidate(lib, parse_ref("x:1"), CatalogSettings(), now=0))
    assert f.notes == [] and (f.parser, f.record.parameter_size) == ("qwen3-coder", "30.5B")
    assert "tools" in f.record.capabilities


def test_unknown_in_a_hf_config_falls_back_to_the_gguf():
    weights, w1 = write_header({"general.architecture": "llama", "general.file_type": 15}), "sha256:" + "1" * 64
    lib = OneModelLibrary({"model_family": "llama", "model_type": "3.21B", "file_type": "unknown"},
                          [_layer("model", weights, w1)], {w1: weights})
    r = asyncio.run(fetch_candidate(lib, parse_ref("x:1"), CatalogSettings(), now=0)).record
    assert (r.quantization, r.parameter_size) == ("Q4_K_M", "3.21B")


def test_an_unreadable_answer_shows_how_it_began():
    weights, w1, p = write_header({"general.architecture": "llama"}), "sha256:" + "1" * 64, "sha256:" + "3" * 64
    junk = b"\x1f\x8b\x08\x00<html>" + b"x" * 100
    lib = OneModelLibrary({}, [_layer("model", weights, w1), _layer("params", junk, p)], {w1: weights, p: junk})
    note = asyncio.run(fetch_candidate(lib, parse_ref("x:1"), CatalogSettings(), now=0)).notes[0]
    assert note.startswith("Parameter nicht lesbar (kein JSON, beginnt mit „·") and "<html>" in note and "…“" in note
