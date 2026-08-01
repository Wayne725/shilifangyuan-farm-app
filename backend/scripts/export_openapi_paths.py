"""Export the API's route table so the App's client can be checked against it.

Run from the `backend/` directory:

    python -m scripts.export_openapi_paths ../src/services/api.routes.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def export(destination: Path) -> list[str]:
    os.environ.setdefault("APP_ENV", "development")
    from app.main import create_app

    schema = create_app().openapi()
    routes = sorted(
        f"{method.upper()} {path}"
        for path, operations in schema["paths"].items()
        for method in operations
        if method.lower()
        in {"get", "post", "put", "patch", "delete"}
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(routes, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return routes


if __name__ == "__main__":
    target = Path(
        sys.argv[1] if len(sys.argv) > 1 else "../src/services/api.routes.json"
    )
    exported = export(target)
    print(f"{len(exported)} routes written to {target}")
