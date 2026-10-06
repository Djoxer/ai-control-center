# Frontend (Angular)

Create the Angular workspace in this folder, then start the dev server with the proxy:

    ng serve --proxy-config proxy.conf.json

The proxy forwards /api to the backend, so there is no CORS setup and SSE works as in production.

Contract: openapi.json in this folder is exported by the backend and committed.
The client in src/app/api/ is generated from it - never edit that folder by hand.

    # after a backend change (in backend/src):
    python -m control_center.export_openapi ../../frontend/openapi.json
    # then here:
    npm run api

Shell endpoints available before any module exists:
- GET /api/v1/health          -> overall status, db, module states
- GET /api/v1/meta/modules    -> menu entries (key, title, icon, order, state)
- GET /api/v1/stream?topics=x -> Server-Sent Events, event name = topic
  (not in openapi.json on purpose: consumed with EventSource, not HttpClient)
