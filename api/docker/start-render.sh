#!/bin/sh
set -eu

# This service only serves the API. Initialize Neon from the local CLI first.
# Enforce the access gate even when configuring the service without a Blueprint.
export ENV=render
export REQUIRE_API_ACCESS_KEY=true

exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-10000}"
