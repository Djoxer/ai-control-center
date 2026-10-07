# Frontend (Angular)

## Development

    npm ci
    ng serve            # http://localhost:4200, /api is proxied to the backend

The proxy (`proxy.conf.mjs`) forwards /api (and /docs, /openapi.json for the API documentation) to the backend, so there is no CORS setup and SSE works
as in production. The target comes from the environment variable `ACC_DEV_BACKEND`:

    # once, persistent for your Windows user (open a new terminal afterwards):
    [Environment]::SetEnvironmentVariable('ACC_DEV_BACKEND', 'http://<ai-box-ip>:8090', 'User')
    # back to the local backend for this terminal only:
    $env:ACC_DEV_BACKEND = ''

Unset or empty means `http://127.0.0.1:8090`. `ng serve` prints the target it uses
(`[proxy] /api -> ...`). The IP never goes into the repo.

VS Code reads user environment variables only when it starts: after setting `ACC_DEV_BACKEND`,
close ALL VS Code windows and reopen - a new terminal tab is not enough. Quick fix for the current
terminal: `$env:ACC_DEV_BACKEND = [Environment]::GetEnvironmentVariable('ACC_DEV_BACKEND', 'User')`.

## UI classes

Repeated Tailwind class strings live in `src/app/ui/tokens.ts` (cards, section titles, fields,
buttons, notices, tone colors). Templates bind them and add layout next to it:

    <article [class]="ui.card" class="p-4">

Tokens carry the look, templates the layout (margins, padding of cards and cells, width). New
repeated element -> new token plus an entry in `UI_DOCS` (the /dev style guide shows every entry).

## Dialogs and menus

Native browser elements, state in Angular signals - no third-party component script.

- `<app-dialog title="…" [open]="x()" (dismiss)="x.set(false)">` - modal with title bar, content and
  an optional `<div dialogActions>` row. Esc, the × button and a backdrop click all end in `(dismiss)`.
- `dialog[appModal]` (`ui/modal.ts`) - the directive underneath, for custom layouts such as the
  mobile sidebar drawer.
- `<app-menu label="…">` (`ui/menu.ts`) - dropdown; trigger content via `[menuTrigger]`, entries are
  elements with `role="menuitem"` and `[class]="ui.menuItem"`. Closes on entry click, outside click,
  Esc and Tab; arrow keys move between entries.

Specs that open dialogs import `src/app/testing/dialog-polyfill.ts` (jsdom has no `showModal()`).

## Developer page /dev

Only under `ng serve` (`isDevMode()`); the production build has no such route. Icon gallery read from
`public/icons.svg` at runtime - groups are the XML comments in the sprite, so a new icon only needs
a `<symbol id="…">` under the right comment. A click copies `<app-icon name="…" class="size-5" />`.
Copying works on http too (`ClipboardService` falls back to a textarea when there is no secure context).

Style guide: one entry per token from `UI_DOCS`, rendered from `src/app/dev/samples.ts`. A sample is
written once (`class="{card} p-4"`, `<icon name="…"/>`) and turned into a live preview, an Angular
snippet (`[class]="ui.card" class="p-4"`) and plain HTML with resolved classes. New token -> new
sample (the `Record<UiToken, Sample>` type refuses to compile without one).

"Alles als Markdown kopieren" / "Als .md speichern": rules, every token with both snippet forms and
the icon names, YAML front matter with version and date - context for an AI chat (~7k tokens).

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
