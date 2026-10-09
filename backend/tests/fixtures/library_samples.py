"""Builds the synthetic model library the FakeLibrary replays (control_center/adapters/library-samples).

Synthetic on purpose: the registry cannot be reached from CI, and a weights file has gigabytes. Each sample
is a manifest, the small files (config, params, template) and the first bytes of the weights file - a GGUF
header with real-looking metadata. Sizes and architectures follow the real models; digests are made up,
except qwen3.5:9b, which reuses the weights digest of the installed qwen3.5:9b of the scenario real-catalog
(the "same weights already on disk" case).

Regenerate after a change (tests/adapters/test_library.py checks the files are up to date):

    uv run python tests/fixtures/library_samples.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from control_center.adapters.library import LIBRARY_SAMPLES_DIR, MANIFEST_ACCEPT  # noqa: E402
from control_center.modules.catalog.gguf import write_header  # noqa: E402

REAL_CATALOG_SHOW = ROOT / "src/control_center/adapters/samples/real-catalog/ollama-show.json"
QWEN35_WEIGHTS = "dec52a44569a2a25341c4e4d3fee25846eed4f6f0b936278e3a3c900bb99d37c"
QWEN35_INSTALLED_SIZE = 6594474711
LICENSE_SIZE = 11338                     # Apache 2.0 - the license blob itself is never read

QWEN3_TEMPLATE = ("{{- if .Tools }}<|im_start|>system\n# Tools\n<tools>\n{{- range .Tools }}\n{{ . }}\n{{- end }}\n"
                  "</tools><|im_end|>\n{{ end }}{{- range .Messages }}<|im_start|>{{ .Role }}\n{{ .Content }}"
                  "<|im_end|>\n{{ end }}<|im_start|>assistant\n{{- if .Think }}\n<think>\n{{ end }}")
MISTRAL_TEMPLATE = ("{{- if .Tools }}[AVAILABLE_TOOLS]{{ json .Tools }}[/AVAILABLE_TOOLS]{{ end }}"
                    "{{- range .Messages }}[INST]{{ .Content }}[/INST]{{ end }}")
GEMMA_TEMPLATE = ("{{- range .Messages }}<start_of_turn>{{ if eq .Role \"assistant\" }}model{{ else }}{{ .Role }}"
                  "{{ end }}\n{{ .Content }}<end_of_turn>\n{{ end }}<start_of_turn>model\n")


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _fake_digest(text: str) -> str:
    return _digest(f"synthetic {text}".encode())


def _tokenizer(count: int) -> dict:
    """Same shape as a real tokenizer section, much shorter (a real one has 150,000+ entries)."""
    tokens = [f"tok{i}" for i in range(count)]
    return {"tokenizer.ggml.model": "gpt2", "tokenizer.ggml.pre": "qwen2", "tokenizer.ggml.tokens": tokens,
            "tokenizer.ggml.token_type": [1] * count,
            "tokenizer.ggml.merges": [f"t o{i}" for i in range(count // 2)],
            "tokenizer.ggml.eos_token_id": 2}


def _qwen35_info() -> dict:
    """The real metadata of qwen3.5:9b (from the /api/show capture), in GGUF order: general, arch, tokenizer."""
    info = json.loads(REAL_CATALOG_SHOW.read_text(encoding="utf-8"))["qwen3.5:9b"]["model_info"]
    kv = {k: v for k, v in info.items() if v not in (None, []) and k != "general.parameter_count"
          and not k.startswith("tokenizer.")}
    for key in ("qwen35.attention.layer_norm_rms_epsilon",):
        kv[key] = float(kv[key])
    kv["qwen35.rope.freq_base"] = float(kv["qwen35.rope.freq_base"])
    return {**kv, **_tokenizer(400)}


MODELS = [
    {   # MoE coder, the AI box's wish list: tools via Ollama's built-in parser, no template layer
        "name": "qwen3-coder:30b", "weights": 18_556_688_736, "tokens": 3000,
        "config": {"model_format": "gguf", "model_family": "qwen3moe", "model_families": ["qwen3moe"],
                   "model_type": "30.5B", "file_type": "Q4_K_M", "renderer": "qwen3-coder", "parser": "qwen3-coder"},
        "params": {"repeat_penalty": 1.05, "stop": ["<|im_start|>", "<|im_end|>", "<|endoftext|>"],
                   "temperature": 0.7, "top_k": 20, "top_p": 0.8},
        "kv": {"general.architecture": "qwen3moe", "general.file_type": 15, "qwen3moe.block_count": 48,
               "qwen3moe.context_length": 262144, "qwen3moe.embedding_length": 2048,
               "qwen3moe.feed_forward_length": 6144, "qwen3moe.attention.head_count": 32,
               "qwen3moe.attention.head_count_kv": 4, "qwen3moe.attention.key_length": 128,
               "qwen3moe.attention.value_length": 128, "qwen3moe.expert_count": 128,
               "qwen3moe.expert_used_count": 8, "qwen3moe.rope.freq_base": 10000000.0},
    },
    {   # the installed qwen3.5:9b of real-catalog: same weights digest, separate vision projector
        "name": "qwen3.5:9b", "weights_digest": "sha256:" + QWEN35_WEIGHTS, "projector": 921_704_832,
        "total": QWEN35_INSTALLED_SIZE, "template": "{{ .Prompt }}",
        "config": {"model_format": "gguf", "model_family": "qwen35", "model_families": ["qwen35"],
                   "model_type": "9.7B", "file_type": "Q4_K_M", "renderer": "qwen3.5", "parser": "qwen3.5",
                   "requires": "0.17.1"},
        "params": {"presence_penalty": 1.5, "temperature": 1, "top_k": 20, "top_p": 0.95},
        "kv": None,                                                  # -> _qwen35_info()
    },
    {   # dense 14B, trained on 40,960 tokens only: fits the card, too short for OpenCode
        "name": "qwen3:14b", "weights": 9_276_198_565, "template": QWEN3_TEMPLATE,
        "config": {"model_format": "gguf", "model_family": "qwen3", "model_families": ["qwen3"],
                   "model_type": "14.8B", "file_type": "Q4_K_M"},
        "params": {"repeat_penalty": 1, "stop": ["<|im_start|>", "<|im_end|>"], "temperature": 0.6, "top_k": 20,
                   "top_p": 0.95},
        "kv": {"general.architecture": "qwen3", "general.file_type": 15, "qwen3.block_count": 40,
               "qwen3.context_length": 40960, "qwen3.embedding_length": 5120, "qwen3.feed_forward_length": 17408,
               "qwen3.attention.head_count": 40, "qwen3.attention.head_count_kv": 8,
               "qwen3.attention.key_length": 128, "qwen3.attention.value_length": 128},
    },
    {   # agent model, 24B: knows tools, but the weights alone nearly fill a 16 GB card
        "name": "devstral:24b", "weights": 14_333_905_760, "template": MISTRAL_TEMPLATE,
        "config": {"model_format": "gguf", "model_family": "llama", "model_families": ["llama"],
                   "model_type": "23.6B", "file_type": "Q4_K_M"},
        "params": {"temperature": 0.15},
        "kv": {"general.architecture": "llama", "general.file_type": 15, "llama.block_count": 40,
               "llama.context_length": 131072, "llama.embedding_length": 5120, "llama.feed_forward_length": 32768,
               "llama.attention.head_count": 32, "llama.attention.head_count_kv": 8,
               "llama.attention.key_length": 128, "llama.attention.value_length": 128},
    },
    {   # vision encoder inside the weights file, sliding window, template without tools
        "name": "gemma3:12b", "weights": 8_149_190_253, "template": GEMMA_TEMPLATE,
        "config": {"model_format": "gguf", "model_family": "gemma3", "model_families": ["gemma3"],
                   "model_type": "12.2B", "file_type": "Q4_K_M"},
        "params": {"stop": ["<end_of_turn>"], "temperature": 1, "top_k": 64, "top_p": 0.95},
        "kv": {"gemma3.attention.head_count": 16, "gemma3.attention.head_count_kv": 8,
               "gemma3.attention.key_length": 256, "gemma3.attention.sliding_window": 1024,
               "gemma3.attention.value_length": 256, "gemma3.block_count": 48, "gemma3.context_length": 131072,
               "gemma3.embedding_length": 3840, "gemma3.feed_forward_length": 15360,
               "gemma3.vision.block_count": 27, "gemma3.vision.embedding_length": 1152,
               "general.architecture": "gemma3", "general.file_type": 15},   # Ollama writes keys sorted
    },
    {   # cloud model: runs at ollama.com, nothing goes onto the card
        "name": "qwen3-coder:480b-cloud", "cloud": True,
        "config": {"model_format": "", "model_family": "", "remote_host": "https://ollama.com:443",
                   "remote_model": "qwen3-coder:480b", "capabilities": ["completion", "tools"],
                   "context_length": 262144},
        "params": {"temperature": 0.7},
    },
]


def _json(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def build() -> dict[str, bytes]:
    """Relative path in library-samples -> content."""
    files: dict[str, bytes] = {}
    index: dict[str, str] = {}
    for m in MODELS:
        name = m["name"]
        layers: list[dict] = []

        def blob(kind: str, data: bytes, ship: bool = True) -> None:
            d = _digest(data)
            layers.append({"mediaType": f"application/vnd.ollama.image.{kind}", "digest": d, "size": len(data)})
            if ship:
                files[f"blobs/{d.replace(':', '-')}"] = data

        config = {**m["config"], "architecture": "amd64", "os": "linux",
                  "rootfs": {"type": "layers", "diff_ids": []}}
        config_bytes = _json(config)
        files[f"blobs/{_digest(config_bytes).replace(':', '-')}"] = config_bytes
        if not m.get("cloud"):
            kv = m["kv"] if m["kv"] is not None else _qwen35_info()
            if m["kv"] is not None:
                kv = {**kv, **_tokenizer(m.get("tokens", 500))}
            header = write_header(kv, tensor_count=0)
            digest = m.get("weights_digest") or _fake_digest(f"{name} model")
            files[f"blobs/{digest.replace(':', '-')}"] = header
            small = [("template", m["template"].encode())] if m.get("template") else []
            small.append(("params", _json(m["params"])))
            fixed = sum(len(d) for _, d in small) + LICENSE_SIZE + len(config_bytes) + m.get("projector", 0)
            weights = m.get("weights") or m["total"] - fixed
            layers.append({"mediaType": "application/vnd.ollama.image.model", "digest": digest, "size": weights,
                           "from": "model.gguf"})
            if m.get("projector"):
                layers.append({"mediaType": "application/vnd.ollama.image.projector",
                               "digest": _fake_digest(f"{name} projector"), "size": m["projector"],
                               "from": "mmproj.gguf"})
            for kind, data in small:
                blob(kind, data)
            layers.append({"mediaType": "application/vnd.ollama.image.license",
                           "digest": _fake_digest(f"{name} license"), "size": LICENSE_SIZE})
        else:
            blob("params", _json(m["params"]))
        manifest = {"schemaVersion": 2, "mediaType": MANIFEST_ACCEPT,
                    "config": {"mediaType": "application/vnd.docker.container.image.v1+json",
                               "digest": _digest(config_bytes), "size": len(config_bytes)},
                    "layers": layers}
        file = name.replace("/", "_").replace(":", "_") + ".json"
        files[f"manifests/{file}"] = _json(manifest)
        index[name] = file
    files["index.json"] = json.dumps(index, indent=2).encode() + b"\n"
    return files


def main() -> None:
    target = LIBRARY_SAMPLES_DIR
    for old in list(target.glob("blobs/*")) + list(target.glob("manifests/*")):
        old.unlink()
    for rel, data in build().items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    print(f"{len(build())} files in {target}")


if __name__ == "__main__":
    main()
