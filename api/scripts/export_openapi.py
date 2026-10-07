"""Export the real FastAPI contract without connecting to a database.

Run from api/: python -m scripts.export_openapi [--check] [--output PATH]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.main import app

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "contracts" / "openapi.json"


def serialized_schema() -> str:
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="Fail when the versioned contract is stale; do not write it.")
    args = parser.parse_args()
    schema = serialized_schema()
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != schema:
            print("OpenAPI contract is stale. Run python -m scripts.export_openapi and update the frontend contract.")
            return 1
        print("OpenAPI contract matches FastAPI.")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(schema, encoding="utf-8", newline="\n")
    print(f"OpenAPI contract exported to {args.output}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
