# Fake scenarios

Each folder is one situation of the AI box, replayed by the fake adapters
(`[adapters] ollama/gpu/host = "fake"`, `fake_scenario = "<folder>"`) and used as test data.

| Folder        | Situation                                                         |
|---------------|-------------------------------------------------------------------|
| `normal`      | coder model + embedding model, both fully in VRAM                 |
| `offload`     | 14b model with large num_ctx, ~84 % on the GPU (Blackwell crash risk) |
| `idle`        | Ollama running, nothing loaded                                    |
| `ollama-down` | no `ollama-*.json` -> the fake reports Ollama as unreachable      |

Files: `ollama-ps.json` / `ollama-version.json` (raw Ollama API answers), `gpu.json`, `host.json`,
`disks.json` (adapter readings), `meta.json` (`capturedAt` shifts expiry times to "now").

The current files are **synthetic** (`"source": "synthetic"` in `meta.json`). Replace them with real
recordings from the AI box:

    cd backend
    uv run python -m control_center.capture_samples src/control_center/adapters/samples/normal

Command lines are stored in "short" mode (no user paths, secret flag values masked).
Check the files before committing anyway.
