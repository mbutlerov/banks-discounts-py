"""Run the frontend contract checks against a real local API and test database."""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import uvicorn

from scripts.contract_server import CONTRACT_DATE, contract_app, require_test_database_url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", type=Path, default=Path(__file__).resolve().parents[3] / "banks-discounts-web")
    args = parser.parse_args()
    require_test_database_url()
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None or not (args.frontend / "package.json").is_file():
        parser.error("A frontend checkout with npm installed is required.")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(contract_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError("Contract API startup failed.")
        environment = {**os.environ, "CONTRACT_API_URL": f"http://127.0.0.1:{port}/api/v1",
                       "CONTRACT_TEST_DATE": CONTRACT_DATE.isoformat()}
        return subprocess.run([npm, "run", "test:contract"], cwd=args.frontend, env=environment,
                              timeout=120, check=False).returncode
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        if thread.is_alive():
            raise RuntimeError("Contract API did not shut down cleanly.")


if __name__ == "__main__":
    raise SystemExit(main())
