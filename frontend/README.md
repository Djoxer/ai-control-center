# Frontend (Angular)

## Development

    npm ci
    ng serve            # http://localhost:4200, /api is proxied to the backend

The proxy (`proxy.conf.mjs`) forwards /api to the backend, so there is no CORS setup and SSE works
as in production. The target comes from the environment variable `ACC_DEV_BACKEND`:

    # once, persistent for your Windows user (open a new terminal afterwards):
    [Environment]::SetEnvironmentVariable('ACC_DEV_BACKEND', 'http://<ai-box-ip>:8090', 'User')
    # back to the local backend for this terminal only:
    $env:ACC_DEV_BACKEND = ''

Unset or empty means `http://127.0.0.1:8090`. `ng serve` prints the target it uses
(`[proxy] /api -> ...`). The IP never goes into the repo.

## Production build

    npm run build       # -> dist/ai-control-center/browser

The backend serves that folder when `frontend_dist` is set in `backend/control-center.toml`.
Hashed bundles are cached for a year, `index.html` is revalidated on every load - after a rebuild
a normal reload shows the new version.

## API client

Contract: `openapi.json` in this folder is exported by the backend and committed.
The client in `src/app/api/` is generated from it - never edit that folder by hand.

    # after a backend change (in backend/src):
    uv run python -m control_center.export_openapi ../../frontend/openapi.json
    # then here:
    npm run api

Live data does not go through the generated client: `GET /api/v1/stream?topics=...` is a
Server-Sent Events stream of unnamed messages `{"topic", "data"}`, consumed by `StreamService`
with one `EventSource` (not in openapi.json on purpose).
