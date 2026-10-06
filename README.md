# AI Control Center

Control plane for the local AI box (Ollama, GPU, model catalog, logs). OpenWebUI stays the work plane.

    ai-control-center/
    ├─ backend/    FastAPI, one process, one uvicorn worker
    ├─ frontend/   Angular, talks only to /api/v1/*
    └─ data/       SQLite + JSON-line logs (gitignored)

## Run (backend)

    cd backend
    copy control-center.example.toml control-center.toml
    uv sync
    uv run python -m control_center

## Develop on a second PC

NVML (GPU) and psutil (host) only see the machine they run on. On the second PC, read Ollama
over the LAN and replay GPU/host from a recorded scenario - in `backend/control-center.toml`:

    [adapters]
    ai_host = "<AI box IP>"
    gpu = "fake"
    host = "fake"
    fake_scenario = "normal"      # normal | offload | idle | ollama-down

`ollama = "fake"` as well gives a dashboard without any AI box (offload demo, presentations).
The UI labels simulated sources via `simulated` in the snapshot.

Record real scenarios on the AI box (replaces the synthetic sample files):

    cd backend
    uv run python -m control_center.capture_samples src/control_center/adapters/samples/normal

## Add a module

1. Create `backend/src/control_center/modules/<key>/`
2. In its `__init__.py` define `MODULE = ModuleSpec(key="<key>", title=..., router=...)`
3. Frontend: one feature folder + one lazy route; the menu entry comes from /api/v1/meta/modules

No registry list, no import in main.py, no config entry needed.

## Tests

    cd backend
    uv run pytest
