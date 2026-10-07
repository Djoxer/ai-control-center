# Fake scenarios

Each folder is one situation of the AI box, replayed by the fake adapters
(`[adapters] ollama/gpu/host = "fake"`, `fake_scenario = "<folder>"`) and used as test data.

| Folder             | Source    | Situation                                                         |
|--------------------|-----------|-------------------------------------------------------------------|
| `normal`           | synthetic | coder model + embedding model, both fully in VRAM                 |
| `offload`          | synthetic | 14b model with large num_ctx, ~84 % on the GPU (Blackwell crash risk) |
| `idle`             | synthetic | Ollama running, nothing loaded                                    |
| `ollama-down`      | synthetic | no `ollama-*.json` -> the fake reports Ollama as unreachable      |
| `real-normal`      | capture   | AI box, only the coder model loaded (`OLLAMA_MAX_LOADED_MODELS=1`) |
| `real-idle`        | capture   | AI box, Ollama running, nothing loaded                            |
| `real-ollama-down` | capture   | AI box, Ollama stopped                                            |

Files: `ollama-ps.json` / `ollama-version.json` (raw Ollama API answers), `gpu.json`, `host.json`,
`disks.json` (adapter readings), `meta.json` (`capturedAt` shifts expiry times to "now", `source`, `note`).

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
