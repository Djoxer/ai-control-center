"""Write the OpenAPI contract to a file, without starting the server.

    python -m control_center.export_openapi ../frontend/openapi.json

The file is committed: the Angular client is generated from it, and a git diff of
openapi.json shows every contract change in review.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from control_center.core.config import load_settings
from control_center.main import create_app


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    # throwaway data dir: exporting must not create a db next to the real one.
    # No logging setup: an open log file in the temp dir would block its cleanup on Windows.
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(load_settings(data_dir=Path(tmp), frontend_dist=None), configure_logging=False)
        spec = app.openapi()          # built from routes, lifespan never runs -> no module startup
    target.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys + trailing newline -> byte-stable output, clean diffs
    target.write_text(json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                      encoding="utf-8", newline="\n")
    print(f"wrote {target} ({len(spec.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
