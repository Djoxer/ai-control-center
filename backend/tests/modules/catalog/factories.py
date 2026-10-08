"""Builders for catalog tests: records and Ollama answers with only the fields a test cares about."""
from __future__ import annotations

from datetime import datetime, timezone

from control_center.adapters.ollama import InstalledModel, ModelDetails
from control_center.modules.catalog.collector import ModelRecord

QWEN2 = {"general.architecture": "qwen2", "qwen2.block_count": 48, "qwen2.context_length": 32768,
         "qwen2.embedding_length": 5120, "qwen2.attention.head_count": 40, "qwen2.attention.head_count_kv": 8}


def rec(name: str, parent: str | None = None, weights: str | None = None, modified: str = "2026-09-01T10:00:00+00:00",
        params: dict[str, list[str]] | None = None, info: dict | None = None, size: int = 1000,
        caps: list[str] | None = None, digest: str | None = None, **extra) -> ModelRecord:
    return ModelRecord(name=name, digest=digest or f"d-{name}", size=size, modified_at=modified, parent_model=parent,
                       weights_digest=weights, parameters=params or {}, model_info=info or {},
                       capabilities=caps if caps is not None else ["completion"], **extra)


def tag(name: str, digest: str | None = None, size: int = 1000, parent: str | None = None) -> InstalledModel:
    return InstalledModel(name=name, digest=digest or f"d-{name}", size=size,
                          modified_at=datetime(2026, 9, 1, tzinfo=timezone.utc), family="qwen2", families=("qwen2",),
                          parameter_size="14.8B", quantization="Q4_K_M", format="gguf", parent_model=parent)


def details(name: str, parent: str | None = None, weights: str | None = "w1", num_ctx: int | None = None,
            info: dict | None = None, caps: tuple[str, ...] = ("completion", "tools")) -> ModelDetails:
    params = {"num_ctx": (str(num_ctx),)} if num_ctx else {}
    return ModelDetails(name=name, parent_model=parent, family="qwen2", families=("qwen2",), parameter_size="14.8B",
                        quantization="Q4_K_M", format="gguf", parameters=params, system_chars=0, template_hash="t1",
                        system_hash=None, weights_digest=weights, model_info=info if info is not None else dict(QWEN2),
                        capabilities=caps)
