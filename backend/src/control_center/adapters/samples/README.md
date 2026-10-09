# Fake scenarios

Each folder is one situation of the AI box, replayed by the fake adapters
(`[adapters] ollama/gpu/host = "fake"`, `fake_scenario = "<folder>"`) and used as test data.

| Folder             | Source    | Situation                                                         |
|--------------------|-----------|-------------------------------------------------------------------|
| `normal`           | synthetic | coder model + embedding model, both fully in VRAM; 8 installed models with derivations |
| `offload`          | synthetic | 14b model with large num_ctx, ~84 % on the GPU (Blackwell crash risk) |
| `idle`             | synthetic | Ollama running, nothing loaded                                    |
| `ollama-down`      | synthetic | no `ollama-*.json` -> the fake reports Ollama as unreachable      |
| `real-normal`      | capture   | AI box, only the coder model loaded (`OLLAMA_MAX_LOADED_MODELS=1`) |
| `real-idle`        | capture   | AI box, Ollama running, nothing loaded                            |
| `real-ollama-down` | capture   | AI box, Ollama stopped                                            |
| `real-catalog`     | capture   | AI box, nothing loaded, all 12 installed models (catalog part b tests: copies, MLA, budget) |

Files: `ollama-ps.json` / `ollama-version.json` / `ollama-tags.json` (raw Ollama API answers),
`ollama-show.json` (model name -> reduced `/api/show` answer, for the catalog), `ollama-chat.json` (raw
`/api/chat` answers to the tool-call check of a test run, by request text: `default` plus per-model overrides -
synthetic in every folder, also in `real-catalog`: the capture tool does not load models), `gpu.json`, `host.json`,
`disks.json` (adapter readings), `meta.json` (`capturedAt` shifts expiry times to "now", `source`, `note`).

`normal`, `offload` and `idle` share the same installed models (`ollama-tags.json`/`ollama-show.json`,
generated once, identical in all three): a base model with two derivations (`parent_model`), one model
attached only through the same weights blob, one whose parent was deleted, a coder without own `num_ctx`,
`gpt-oss:20b` (sliding window) and the embedding model. `real-catalog` is the real inventory: catalog tests
check a few of its values (12 models, 1424 MiB of other programs, gpt-oss "tight" at 64k) - re-recording it
means updating those tests. Captures made before the catalog existed have no
`ollama-tags.json`: the catalog then reports the file as missing - record the scenario again.

## Two kinds of folders

- **Synthetic** (`"source": "synthetic"`): hand-made, tests check their exact values. Never overwrite
  them with a capture - the capture tool refuses unless `--force` is given.
- **Captures** (`"source": "capture"`, names start with `real-`): recorded on the AI box, free to
  re-record. Only the value-free test `tests/adapters/test_samples.py` runs over them.
  `offload` stays synthetic: provoking a partial offload on the Blackwell card can crash the runner.

## Recording

On the AI box (NVML and psutil only see the local machine), in `backend/`:

    uv run python -m control_center.capture_samples real-normal --note "only the coder model"

The argument is the scenario name, not a path - files always land in this folder.
Command lines are stored in "short" mode (no user paths, secret flag values masked) and venv
launcher twins are hidden. Check `host.json` before committing anyway: plain arguments
(URLs, names) are not masked.

`ollama-show.json` is reduced on purpose: system prompts become `<system prompt removed: N characters>`,
Modelfiles keep only `FROM`/`ADAPTER`/`PARAMETER` lines with blob paths as `<blobs>/sha256-…`, license
texts shrink to their first line, MESSAGE lines are dropped. Model NAMES stay - check
`ollama-tags.json` before committing if a model is named after a customer project.

The model library of the candidate check (`[adapters] library = "fake"`) is not part of a scenario - the
registry is outside the AI box. Its synthetic samples live in `../library-samples` (see the README there).
