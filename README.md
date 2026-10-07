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
    fake_scenario = "normal"      # see backend/src/control_center/adapters/samples/README.md

`ollama = "fake"` as well gives a dashboard without any AI box (offload demo, presentations).
The UI labels simulated sources via `simulated` in the snapshot.

Record a real scenario on the AI box - the argument is a name, the files land in `adapters/samples/<name>/`.
Use a `real-` prefix; the synthetic folders are test data and stay untouched:

    cd backend
    uv run python -m control_center.capture_samples real-normal --note "only the coder model"

Frontend against a backend on another machine: set `ACC_DEV_BACKEND` (see `frontend/README.md`),
then `ng serve`. No IP in `proxy.conf.mjs`, nothing to revert before a commit.

## Serve the frontend from the backend (AI box)

    cd frontend
    npm ci
    npm run build

Then in `backend/control-center.toml` (top level, above the first `[section]`):

    frontend_dist = "../frontend/dist/ai-control-center/browser"

Restart the backend; the log says `serving frontend from ...`. After a later `git pull` with frontend
changes: `npm ci` (if package-lock.json changed), `npm run build`, no backend restart needed.

## Add a module

1. Create `backend/src/control_center/modules/<key>/`
2. In its `__init__.py` define `MODULE = ModuleSpec(key="<key>", title=..., router=...)`
3. Frontend: one feature folder + one lazy route; the menu entry comes from /api/v1/meta/modules

No registry list, no import in main.py, no config entry needed.

## Help and changelog

Every module explains itself in a `HELP.md` next to its code (`backend/src/control_center/modules/<key>/HELP.md`,
general part in `core/HELP.md`), German, two parts: `## Bedienung` (using the page) and `## Betrieb`
(configuration, operation). The backend serves the files of all switched-on modules at `/api/v1/help`;
a new module without `HELP.md` fails `tests/core/test_help.py`. The frontend shows them at `/help`
(⋮ menu): `/help?doc=<module key>` opens one module, `/help?doc=changelog` "Was ist neu".

"Was ist neu" is `CHANGELOG.md` in the repo root, generated from the commit messages (`cliff.toml`):

    uvx git-cliff -o CHANGELOG.md

Only `feat`, `fix` and `perf` commits become entries - write their subject for the people using the app.

## Release

1. `version` in `backend/pyproject.toml`, then `uv sync` in `backend/` (updates `uv.lock`). Commit it
   with the last feature of the release (or alone as `chore(release): vX.Y.Z`).
2. `uvx git-cliff --tag vX.Y.Z -o CHANGELOG.md` (repo root). git-cliff reads commits, not files:
   everything that belongs to the release must be committed before this step.
3. `git add CHANGELOG.md`, `git commit -m "docs: changelog for vX.Y.Z"`, `git tag -a vX.Y.Z -m "..."`,
   `git push --follow-tags`

git-cliff reads the repository through libgit2, which refuses a folder owned by another Windows user
or group ("not owned by current user"). Allow this one folder:
`git config --global --add safe.directory D:/path/to/ai-control-center` (forward slashes).

## Tests

    cd backend
    uv run pytest
