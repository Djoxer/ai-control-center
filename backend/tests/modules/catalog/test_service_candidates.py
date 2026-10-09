"""Candidates in the CatalogService: the same estimate as installed models, calibration through shared weights,
what the page is told about installed twins, the minimum Ollama version, keeping and forgetting."""
import json
import time

import pytest

from control_center.adapters.library import BadReference, LibraryNotFound, LibraryUnavailable, parse_manifest
from control_center.modules.catalog.gguf import write_header
from control_center.modules.catalog.service import TOPIC, UnknownModel
from control_center.modules.catalog.suitability import CANDIDATE_NO_TOOLS, CANDIDATE_UNTESTED

from .factories import QWEN2
from .test_service import GIB, Harness, _ready, run, running

TOOLS_TEMPLATE = "{{ if .Tools }}{{ .Tools }}{{ end }}{{ .Prompt }}"


class StubLibrary:
    """name -> (config, layers, blobs). Digests are free text after 'sha256:' - the weights digest of an
    installed stub model is "w", so a layer "sha256:w" means: the very same weights file."""
    simulated = False

    def __init__(self):
        self.models: dict[str, tuple[dict, list[dict], dict[str, bytes]]] = {}
        self.fail: Exception | None = None

    def add(self, name, weights="sha256:other", size=8 * GIB, info=None, template=TOOLS_TEMPLATE, config=None,
            params=None, cloud=False):
        blobs, layers = {}, []
        if not cloud:
            blobs[weights] = write_header(info if info is not None else QWEN2)
            layers.append({"mediaType": "application/vnd.ollama.image.model", "digest": weights, "size": size})
        for kind, data in (("template", template.encode() if template is not None else None),
                           ("params", json.dumps(params).encode() if params is not None else None)):
            if data is not None:
                digest = f"sha256:{name}-{kind}"
                blobs[digest] = data
                layers.append({"mediaType": f"application/vnd.ollama.image.{kind}", "digest": digest,
                               "size": len(data)})
        self.models[name] = (config or {"model_family": "qwen2", "model_type": "14.8B", "file_type": "Q4_K_M"},
                             layers, blobs)

    async def manifest(self, ref, timeout):
        if self.fail:
            raise self.fail
        if ref.name not in self.models:
            raise LibraryNotFound(f"{ref.name} gibt es nicht")
        config, layers, blobs = self.models[ref.name]
        blobs["sha256:config"] = json.dumps(config).encode()
        return parse_manifest(json.dumps({"config": {"digest": "sha256:config", "size": 100},
                                          "layers": layers}).encode())

    async def blob(self, ref, digest, start, length, timeout):
        return self.models[ref.name][2].get(digest, json.dumps(self.models[ref.name][0]).encode())[start:start + length]


def _harness(tmp_path, **cfg):
    h = Harness(tmp_path, **cfg)
    h.library = StubLibrary()
    return h


def test_a_candidate_with_installed_weights_is_calibrated_like_its_twin(tmp_path):
    h = _harness(tmp_path)
    h.ollama.install("coder:14b", weights="w")
    h.library.add("coder:14b", weights="sha256:w")              # the same name, the same weights file
    h.library.add("coder-fast:14b", weights="sha256:w", params={"num_ctx": 16384, "temperature": 0.2})

    async def go():
        svc = await _ready(h)
        h.ollama.ps = [running("coder:14b", digest="d-coder:14b", ctx=32768, size=10 * GIB, vram=10 * GIB)]
        await svc.observe()
        before = len(h.published)
        assert await svc.check_candidate("ollama pull Coder:14B") == "coder:14b"
        assert await svc.check_candidate("coder-fast:14b") == "coder-fast:14b"
        assert len(h.published) == before + 2 and h.published[-1][0] == TOPIC
        ov = await svc.overview()
        twin = next(m for m in ov.models if m.name == "coder:14b")
        fast, same = ov.candidates                                # newest check first
        assert (same.name, same.installed, same.same_weights) == ("coder:14b", True, ["coder:14b"])
        assert same.notes[0] == "Schon installiert – mit genau diesen Gewichten."
        assert same.pull == "ollama pull coder:14b" and same.simulated is False
        # the estimate is the installed model's: calibrated by its measurement, same context, same need
        assert "1 Messung" in same.estimate.calibrated
        assert same.context.effective == twin.context.effective == 4096
        assert same.verdict.need_bytes == twin.estimate.need_bytes and same.verdict.basis == "estimated"
        assert (fast.installed, fast.same_weights) == (False, ["coder:14b"])
        assert fast.notes[0] == "Die Gewichte liegen schon auf der Platte (coder:14b) – ein Pull lädt nur den Rest."
        assert fast.context.effective == 16384 and fast.context.source == "model"
        assert fast.parameters == {"num_ctx": ["16384"], "temperature": ["0.2"]}
        # steps: every context up to the trained 32k, need growing, the 32k step = the measurement
        assert [s.tokens for s in same.steps] == [2048, 4096, 8192, 16384, 24576, 32768]
        needs = [s.need_bytes for s in same.steps]
        assert needs == sorted(needs) and len(set(needs)) == len(needs) and needs[-1] == 10 * GIB
        assert same.fits_up_to == 32768 and same.loads_up_to == 32768
        # OpenCode: tools in the template, but trained on 32k only
        assert same.opencode.state == "no" and "Trainiert auf 32.768 Token" in " ".join(same.opencode.reasons)
        assert CANDIDATE_UNTESTED in same.opencode.reasons

    run(go())


def test_what_the_page_is_told_about_versions_twins_and_the_card(tmp_path):
    h = _harness(tmp_path)
    h.ollama.install("coder:14b", weights="w")
    h.library.add("coder:14b", weights="sha256:new", config={"requires": "0.40.0"})
    h.library.add("big:70b", size=40 * GIB)
    h.library.add("chat:7b", size=4 * GIB, template="{{ .Prompt }}")          # template without tools
    h.library.add("plain:7b", size=4 * GIB, template=None, params={"num_ctx": 65536},
                  info={**QWEN2, "qwen2.block_count": 8, "qwen2.context_length": 131072})
    h.library.add("embed:1b", size=GIB, info={**QWEN2, "qwen2.pooling_type": 1})
    h.library.add("cloud:480b", cloud=True, config={"remote_host": "https://ollama.com:443"})

    async def go():
        svc = await _ready(h)
        for name in ("coder:14b", "big:70b", "chat:7b", "plain:7b", "embed:1b", "cloud:480b"):
            await svc.check_candidate(name)
        c = {x.name: x for x in (await svc.overview()).candidates}
        newer = c["coder:14b"]
        assert newer.installed and newer.same_weights == [] and newer.requires_ok is False
        assert newer.notes[:2] == ["Braucht Ollama ≥ 0.40.0, installiert ist 0.35.0 – erst Ollama aktualisieren.",
                                   "Installiert ist ein anderer Stand (andere Gewichte) – ein Pull würde ihn ersetzen."]
        big = c["big:70b"]
        assert big.verdict.state == "split" and big.fits_up_to is None and big.loads_up_to is None
        assert {s.state for s in big.steps} == {"split"} and big.opencode.state == "no"
        assert c["chat:7b"].opencode.state == "no" and CANDIDATE_NO_TOOLS in c["chat:7b"].opencode.reasons
        plain = c["plain:7b"]                                     # no template, no parser: open question
        assert "tools" not in plain.capabilities and plain.opencode.state == "unknown"   # context and card fine
        assert plain.opencode.reasons[:2] == ["Noch offen: Tool-Calls ungeprüft", CANDIDATE_UNTESTED]
        assert plain.steps[-1].tokens == 131072
        assert c["embed:1b"].capabilities == ["embedding"] and c["embed:1b"].opencode is None
        cloud = c["cloud:480b"]
        assert cloud.error.startswith("Cloud-Modell") and cloud.verdict is None and cloud.steps == []
        assert cloud.context is None and cloud.opencode is None

    run(go())


def test_the_steps_follow_the_card(tmp_path):
    """Same weights, budget of 14.1 GiB: 8 GiB + KV fit up to a point, then 'knapp', then split."""
    h = _harness(tmp_path)
    h.library.add("mid:14b", size=8 * GIB, info={**QWEN2, "qwen2.context_length": 131072})

    async def go():
        svc = await _ready(h)
        await svc.check_candidate("mid:14b")
        cand = (await svc.overview()).candidates[0]
        states = [s.state for s in cand.steps]
        assert states == sorted(states, key=["fits", "tight", "split"].index)     # never better with more context
        assert {"fits", "split"} <= set(states)
        last_fit = max(s.tokens for s in cand.steps if s.state == "fits")
        assert cand.fits_up_to == last_fit
        assert cand.loads_up_to == max(s.tokens for s in cand.steps if s.state in ("fits", "tight"))
        assert all(s.need_bytes is not None and s.extra_bytes == 0 for s in cand.steps)

    run(go())


def test_candidates_are_kept_replaced_and_forgotten(tmp_path):
    h = _harness(tmp_path, keep_candidates=2)
    for name in ("a:1", "b:1", "c:1"):
        h.library.add(name)

    async def go():
        svc = await _ready(h)
        await svc.check_candidate("a:1")
        await svc.check_candidate("b:1")
        await svc.check_candidate("a:1")                            # again: replaces, newest now
        assert [c.name for c in (await svc.overview()).candidates] == ["a:1", "b:1"]
        await svc.check_candidate("c:1")                            # over the limit: the oldest check goes
        assert [c.name for c in (await svc.overview()).candidates] == ["c:1", "a:1"]
        again = await h.service()                                  # kept in SQLite
        assert sorted(again.candidates) == ["a:1", "c:1"]
        before = len(h.published)
        await svc.forget_candidate("c:1")
        assert len(h.published) == before + 1 and list(svc.candidates) == ["a:1"]
        assert list((await h.service()).candidates) == ["a:1"]
        with pytest.raises(UnknownModel):
            await svc.forget_candidate("c:1")

    run(go())


def test_failed_checks_keep_nothing(tmp_path):
    h = _harness(tmp_path)

    async def go():
        svc = await _ready(h)
        before = len(h.published)
        with pytest.raises(BadReference):
            await svc.check_candidate("evil.example/x/y")
        with pytest.raises(LibraryNotFound):
            await svc.check_candidate("nope:1b")
        h.library.fail = LibraryUnavailable("registry.ollama.ai nicht erreichbar")
        with pytest.raises(LibraryUnavailable):
            await svc.check_candidate("a:1")
        assert svc.candidates == {} and len(h.published) == before
        assert (await h.service()).candidates == {}

    run(go())


def test_a_broken_stored_candidate_is_skipped(tmp_path):
    h = _harness(tmp_path)
    h.library.add("a:1")

    async def go():
        svc = await _ready(h)
        await svc.check_candidate("a:1")
        # another version wrote it - and later than a:1, so it is read first and must not stop the rest
        await svc.repo.save_candidate("broken:1", time.time() + 100, {"name": "broken:1"}, keep=10)
        again = await h.service()
        assert list(again.candidates) == ["a:1"]

    run(go())
