"""Catalog settings: [modules.catalog] in control-center.toml.

Where Ollama lives is NOT configured here but in [adapters] (shared by all modules). This table only
says how often the catalog looks, and which assumptions the VRAM estimate makes - every assumption
is a number here, so a better measurement replaces a guess without touching code.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

KvCacheType = Literal["f16", "q8_0", "q4_0"]


class CatalogSettings(BaseModel):
    # how often: /api/tags is cheap, /api/show only runs for new or changed models
    refresh_interval_s: float = Field(60, ge=10, le=3600)
    # /api/ps: which model runs with which context and how much VRAM it takes = "gemessen" values
    observe_interval_s: float = Field(10, ge=2, le=600)
    show_timeout_s: float = Field(10, gt=0, le=120)            # one /api/show call

    # What Ollama does when a model has no own value. None = read from the "server config" line in
    # Ollama's server.log (only possible when Ollama runs on this machine); a value here wins.
    server_context_length: int | None = Field(None, ge=256, le=4_194_304)   # OLLAMA_CONTEXT_LENGTH
    kv_cache_type: KvCacheType | None = None                                 # OLLAMA_KV_CACHE_TYPE
    flash_attention: bool | None = None                                      # OLLAMA_FLASH_ATTENTION
    num_parallel: int | None = Field(None, ge=1, le=64)                      # OLLAMA_NUM_PARALLEL
    ollama_log_paths: list[str] = Field(default_factory=lambda: ["%LOCALAPPDATA%/Ollama/server*.log"])
    # neither the model nor the server names a context length: assume this (shown as an assumption)
    fallback_context_length: int = Field(4096, ge=256, le=4_194_304)
    # Ollama cuts num_ctx to the context length the model was trained with (log line
    # "requested context size too large for model"). false = estimate with the uncut value.
    clamp_to_trained: bool = True

    # Need of a model (Ollama's scale) = weights + KV cache + graph_reserve, until a measurement calibrates it
    graph_reserve_gib: float = Field(0.4, ge=0, le=16)         # compute buffers at flash attention, rough
    # Budget of the card = total - other programs - Ollama's reserve. Other programs are MEASURED whenever
    # Ollama has nothing loaded (desktop, browser ...: 1.4 GiB on the AI box on 08.10.); this is the fallback.
    other_usage_gib: float = Field(1.2, ge=0, le=64)
    ollama_reserve_gib: float = Field(0.45, ge=0, le=16)       # Ollama keeps ~457 MiB per CUDA GPU free (assumption)
    tight_ratio: float = Field(0.9, gt=0, le=1)                # above this share of the budget = "knapp"

    # Test runs: load a model with a chosen context, answer a fixed prompt, measure speed and memory.
    # Locked when Ollama runs on another machine - a test unloads the chat model of whoever works there.
    allow_remote_tests: bool = False
    test_prompt: str = Field(
        "Schreibe eine TypeScript-Funktion, die eine Liste von Bestellungen nach Kunde gruppiert und je Kunde "
        "die Summe bildet. Nur Code mit kurzen Kommentaren.", min_length=1)
    test_num_predict: int = Field(128, ge=8, le=4096)           # tokens to generate: enough for a stable tok/s
    test_timeout_s: float = Field(300, gt=0, le=3600)          # loading a big model from disk takes a while
    unload_after_test: bool = True                              # leave the GPU as free as before
    keep_tests: int = Field(200, ge=10, le=10_000)              # test results kept in SQLite (all models)
    # Tool-call check inside every test run (three agent-like requests via /api/chat). Thinking models reason
    # before they call a tool - the budget must leave room for that.
    test_tools: bool = True
    test_tool_num_predict: int = Field(2048, ge=64, le=16_384)

    # OpenCode suitability: the context an agent session needs, and the output share of it the generated
    # opencode.json block reserves (limit.output). Without limit.context, OpenCode assumes a huge window and
    # Ollama silently cuts everything beyond num_ctx.
    opencode_min_context: int = Field(65536, ge=4096, le=4_194_304)
    opencode_output_tokens: int = Field(8192, ge=256, le=262_144)

    # Candidate check: manifest, small files and the GGUF header of a model in Ollama's library, BEFORE the pull.
    # Where to ask is [adapters] library / library_hosts. The header usually ends after 1-8 MiB (the tokenizer
    # tables come first in many files); the limit stops a broken file from streaming gigabytes.
    registry_timeout_s: float = Field(20, gt=0, le=300)        # per request to the registry
    registry_header_max_mib: int = Field(32, ge=1, le=512)
    keep_candidates: int = Field(30, ge=1, le=500)              # checked candidates kept (oldest dropped)

    # deleted models stay listed (with their measurements) for this long
    keep_removed_days: int = Field(90, ge=0, le=3650)

    @model_validator(mode="after")
    def _intervals(self) -> "CatalogSettings":
        if self.observe_interval_s > self.refresh_interval_s:
            raise ValueError("observe_interval_s must not be larger than refresh_interval_s")
        return self
